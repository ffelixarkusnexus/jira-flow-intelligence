from datetime import UTC, datetime

from app.core.config import Settings
from app.services.insight_service import detect_bottleneck, generate_insight_report
from app.services.metrics_service import StatusWindowResult, WindowSnapshot


def _snap(stats: dict[str, dict]) -> WindowSnapshot:
    snap = WindowSnapshot(
        window_start=datetime(2026, 1, 1, tzinfo=UTC),
        window_end=datetime(2026, 1, 8, tzinfo=UTC),
    )
    for status, v in stats.items():
        # In real data sample_size tracks the count of issues passing through
        # the status during the window; for tests it defaults to throughput
        # so trend filtering (>= MIN_PRIOR_SAMPLE) doesn't suppress fixtures
        # that only declare throughput.
        snap.statuses[status] = StatusWindowResult(
            status=status,
            window_start=snap.window_start,
            window_end=snap.window_end,
            avg_seconds=v.get("avg", 0.0),
            p50_seconds=v.get("p50", 0.0),
            p90_seconds=v.get("p90", 0.0),
            wip_avg=v.get("wip", 0.0),
            throughput=v.get("throughput", 0),
            sample_size=v.get("n", v.get("throughput", 0)),
        )
    return snap


def test_scoring_review_is_clear_bottleneck_high_confidence():
    settings = Settings()
    cur = _snap(
        {
            "Review": {"avg": 14000, "wip": 13.0, "throughput": 7},
            "In Progress": {"avg": 10000, "wip": 8.0, "throughput": 10},
        }
    )
    prev = _snap(
        {
            "Review": {"avg": 10000, "wip": 10.0, "throughput": 10},
            "In Progress": {"avg": 9500, "wip": 8.0, "throughput": 10},
        }
    )
    bottleneck, _candidates = detect_bottleneck(cur, prev, settings)
    assert bottleneck is not None
    assert bottleneck.status == "Review"
    # time_ratio=1.4 -> +2; wip_ratio=1.3 -> +1; throughput_delta=-0.3 -> +1; total=4 -> high
    assert bottleneck.score == 4
    assert bottleneck.confidence == "high"


def test_scoring_extra_weight_when_time_ratio_very_high():
    settings = Settings()
    cur = _snap({"Review": {"avg": 16000, "wip": 13.0, "throughput": 5}})
    prev = _snap({"Review": {"avg": 10000, "wip": 10.0, "throughput": 10}})
    bottleneck, _ = detect_bottleneck(cur, prev, settings)
    assert bottleneck is not None
    # time_ratio=1.6 → +2 (>=1.3) and +1 (>=1.5); wip=1.3 → +1; throughput=-0.5 → +1; total=5
    assert bottleneck.score == 5
    assert bottleneck.confidence == "very_high"


def test_no_bottleneck_when_score_below_threshold():
    settings = Settings()
    cur = _snap({"Review": {"avg": 11000, "wip": 10.0, "throughput": 9}})
    prev = _snap({"Review": {"avg": 10000, "wip": 10.0, "throughput": 10}})
    # time_ratio=1.1 → 0; wip_ratio=1.0 → 0; throughput_delta=-0.1 → 0
    bottleneck, _ = detect_bottleneck(cur, prev, settings)
    assert bottleneck is None


def test_skips_status_with_no_previous_window_data():
    settings = Settings()
    cur = _snap({"NewStatus": {"avg": 99999, "wip": 99.0, "throughput": 1}})
    prev = _snap({})
    bottleneck, candidates = detect_bottleneck(cur, prev, settings)
    assert bottleneck is None
    assert candidates == []


def test_picks_highest_score_across_statuses():
    settings = Settings()
    cur = _snap(
        {
            "Review": {"avg": 14000, "wip": 13.0, "throughput": 7},  # score 4
            "In Progress": {"avg": 16000, "wip": 13.0, "throughput": 5},  # score 5
        }
    )
    prev = _snap(
        {
            "Review": {"avg": 10000, "wip": 10.0, "throughput": 10},
            "In Progress": {"avg": 10000, "wip": 10.0, "throughput": 10},
        }
    )
    bottleneck, _ = detect_bottleneck(cur, prev, settings)
    assert bottleneck is not None
    assert bottleneck.status == "In Progress"
    assert bottleneck.score == 5


def test_trends_classify_direction():
    settings = Settings()
    cur = _snap({"Review": {"avg": 13000, "wip": 12.0, "throughput": 12}})
    prev = _snap({"Review": {"avg": 10000, "wip": 10.0, "throughput": 10}})
    report = generate_insight_report(cur, prev, settings)
    by_metric = {(t.metric, t.status): t for t in report.trends}
    # avg time +30% → time worsening
    assert by_metric[("avg_time", "Review")].direction == "worsening"
    # wip +20% → worsening
    assert by_metric[("wip", "Review")].direction == "worsening"
    # throughput +20% → improving (higher is better)
    assert by_metric[("throughput", "Review")].direction == "improving"


def test_trends_suppressed_when_prior_sample_too_small():
    """A status that had only 1 issue in the prior window must not produce a
    trend row even when current values look dramatic. This is what kept
    example-tenant' dashboard showing +33,701,452% noise on quasi-empty
    statuses."""
    settings = Settings()
    cur = _snap({"IN TESTING": {"avg": 86400, "wip": 5.0, "throughput": 8, "n": 8}})
    # Prior had a single issue with a tiny avg — the kind of artifact that
    # produces ridiculous percentages downstream.
    prev = _snap({"IN TESTING": {"avg": 0.001, "wip": 0.001, "throughput": 1, "n": 1}})
    report = generate_insight_report(cur, prev, settings)
    statuses_with_trends = {(t.metric, t.status) for t in report.trends}
    assert ("avg_time", "IN TESTING") not in statuses_with_trends
    assert ("wip", "IN TESTING") not in statuses_with_trends
    assert ("throughput", "IN TESTING") not in statuses_with_trends


def test_detect_bottleneck_excludes_terminal_statuses():
    """Regression: terminal statuses (Done, etc.) accumulate tickets and
    their time-in-status grows without bound, producing math-artifact
    "bottleneck" scores. detect_bottleneck must exclude them via the
    per-tenant terminal_statuses config (same exclusion compute_trends uses).

    Observed 2026-06-01 on example-tenant: 'Done is the current bottleneck'
    with VERY HIGH CONFIDENCE and +93,514% time. Done was in terminal_statuses
    but the detector wasn't filtering."""
    settings = Settings()
    # Done scores extreme (would dominate without the filter); Review is the
    # real bottleneck signal.
    cur = _snap(
        {
            "Done": {"avg": 9_999_999, "wip": 99.0, "throughput": 50},
            "Review": {"avg": 14000, "wip": 13.0, "throughput": 7},
        }
    )
    prev = _snap(
        {
            "Done": {"avg": 100, "wip": 1.0, "throughput": 10},  # massive ratio if scored
            "Review": {"avg": 10000, "wip": 10.0, "throughput": 10},
        }
    )
    bottleneck, candidates = detect_bottleneck(cur, prev, settings)
    assert bottleneck is not None
    assert bottleneck.status == "Review"  # not Done
    # And Done doesn't even appear in the candidate list.
    assert all(c.status != "Done" for c in candidates)


def test_detect_bottleneck_terminal_match_is_case_insensitive():
    """DONE / Done / done all collapse via casefold() so a tenant config that
    lists only one case still excludes the other (example-tenant had both
    "Done" and "DONE" variants in the status list)."""
    settings = Settings()
    cur = _snap(
        {
            "DONE": {"avg": 9_999_999, "wip": 99.0, "throughput": 50},
            "Review": {"avg": 14000, "wip": 13.0, "throughput": 7},
        }
    )
    prev = _snap(
        {
            "DONE": {"avg": 100, "wip": 1.0, "throughput": 10},
            "Review": {"avg": 10000, "wip": 10.0, "throughput": 10},
        }
    )
    bottleneck, _ = detect_bottleneck(cur, prev, settings)
    assert bottleneck is not None
    assert bottleneck.status == "Review"


def test_trends_filter_terminal_statuses():
    """Terminal statuses (Done, Cancelled, etc.) accumulate tickets and
    don't represent flow, so they must never appear in the trend list even
    when the per-status sample is large enough. Per ux-fix-sprint item #6."""
    settings = Settings()
    cur = _snap(
        {
            "Done": {"avg": 86400, "wip": 5.0, "throughput": 12, "n": 12},
            "Review": {"avg": 7200, "wip": 4.0, "throughput": 5, "n": 5},
        }
    )
    prev = _snap(
        {
            "Done": {"avg": 3600, "wip": 2.0, "throughput": 8, "n": 8},
            "Review": {"avg": 3600, "wip": 2.0, "throughput": 5, "n": 5},
        }
    )
    report = generate_insight_report(cur, prev, settings)
    by_key = {(t.metric, t.status) for t in report.trends}
    assert ("avg_time", "Done") not in by_key
    assert ("wip", "Done") not in by_key
    assert ("throughput", "Done") not in by_key
    # Sanity: non-terminal "Review" still appears.
    assert ("avg_time", "Review") in by_key


def test_trends_suppressed_when_prior_baseline_below_meaningfulness_floor():
    """Even with enough sample size, a 5-minute prior avg → 55-minute current
    avg is +1000% but the absolute change is trivial. Below the 30-min
    meaningfulness floor, the avg_time entry is suppressed. Per ux-fix-sprint
    item #6."""
    settings = Settings()
    cur = _snap({"Review": {"avg": 3300, "wip": 5.0, "throughput": 5, "n": 5}})
    # 5-minute (300s) prior avg — below the 30-min (1800s) floor.
    prev = _snap({"Review": {"avg": 300, "wip": 3.0, "throughput": 5, "n": 5}})
    report = generate_insight_report(cur, prev, settings)
    by_key = {(t.metric, t.status) for t in report.trends}
    assert ("avg_time", "Review") not in by_key
    # wip and throughput baselines are above their floors, so they remain.
    assert ("wip", "Review") in by_key


# ----- ADR-0042: external-blocking (pause) statuses -----------------------


def test_detect_bottleneck_with_no_external_blocking_statuses_matches_existing_behavior():
    """Default `external_blocking_statuses = []` preserves current behavior:
    the highest-signal status wins attribution, no exclusion applied."""
    settings = Settings()
    assert settings.external_blocking_statuses == []
    cur = _snap(
        {
            "Blocked": {"avg": 50_000, "wip": 20.0, "throughput": 7},
            "Review": {"avg": 14000, "wip": 13.0, "throughput": 7},
        }
    )
    prev = _snap(
        {
            "Blocked": {"avg": 10_000, "wip": 5.0, "throughput": 10},
            "Review": {"avg": 10000, "wip": 10.0, "throughput": 10},
        }
    )
    bottleneck, candidates = detect_bottleneck(cur, prev, settings)
    assert bottleneck is not None
    # With Blocked unfiltered, it has the highest signal and wins attribution.
    assert bottleneck.status == "Blocked"
    # And Blocked is in the candidate list.
    assert any(c.status == "Blocked" for c in candidates)


def test_detect_bottleneck_skips_external_blocking_statuses_from_attribution():
    """When a status is in `external_blocking_statuses`, it does not win
    attribution even when its raw signal is dominant — the bottleneck card
    names the next-highest non-paused status instead."""
    settings = Settings(external_blocking_statuses=["Blocked"])
    cur = _snap(
        {
            "Blocked": {"avg": 50_000, "wip": 20.0, "throughput": 7},
            "Review": {"avg": 14000, "wip": 13.0, "throughput": 7},
        }
    )
    prev = _snap(
        {
            "Blocked": {"avg": 10_000, "wip": 5.0, "throughput": 10},
            "Review": {"avg": 10000, "wip": 10.0, "throughput": 10},
        }
    )
    bottleneck, candidates = detect_bottleneck(cur, prev, settings)
    assert bottleneck is not None
    # Blocked is filtered; Review wins attribution.
    assert bottleneck.status == "Review"
    # And Blocked does not appear in the candidate list at all.
    assert all(c.status != "Blocked" for c in candidates)


def test_detect_bottleneck_case_folds_external_blocking_match():
    """A tenant who lists "BLOCKED" still matches an actual "Blocked" slice
    (and vice versa). Mirrors the case-folding the terminal_statuses filter
    already does — the runbook records past tenants who had multiple casings
    in their status list."""
    settings = Settings(external_blocking_statuses=["BLOCKED"])
    cur = _snap(
        {
            "Blocked": {"avg": 50_000, "wip": 20.0, "throughput": 7},
            "Review": {"avg": 14000, "wip": 13.0, "throughput": 7},
        }
    )
    prev = _snap(
        {
            "Blocked": {"avg": 10_000, "wip": 5.0, "throughput": 10},
            "Review": {"avg": 10000, "wip": 10.0, "throughput": 10},
        }
    )
    bottleneck, _ = detect_bottleneck(cur, prev, settings)
    assert bottleneck is not None
    assert bottleneck.status == "Review"


def test_external_blocking_set_does_not_affect_trends_or_data():
    """Per ADR-0042, the exclusion lives only in attribution. Trends and
    slice/chart data are unaffected — `compute_trends` still surfaces
    external-blocking statuses, and the candidate list still computes
    StatusSignals for non-excluded statuses regardless of the external set."""
    from app.services.insight_service import compute_trends

    settings = Settings(external_blocking_statuses=["Blocked"])
    cur = _snap(
        {
            "Blocked": {"avg": 50_000, "wip": 20.0, "throughput": 7, "n": 7},
            "Review": {"avg": 14000, "wip": 13.0, "throughput": 7, "n": 7},
        }
    )
    prev = _snap(
        {
            "Blocked": {"avg": 10_000, "wip": 5.0, "throughput": 10, "n": 10},
            "Review": {"avg": 10000, "wip": 10.0, "throughput": 10, "n": 10},
        }
    )
    trends = compute_trends(cur, prev, settings)
    # Trends still include external-blocking — they capture global flow
    # signal, not bottleneck attribution.
    statuses_with_trends = {t.status for t in trends}
    assert "Blocked" in statuses_with_trends
