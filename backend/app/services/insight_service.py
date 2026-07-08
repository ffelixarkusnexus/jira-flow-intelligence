from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from app.services.metrics_service import StatusWindowResult, WindowSnapshot

Confidence = Literal["medium", "high", "very_high"]


class _Thresholds(Protocol):
    """Structural type satisfied by both `Settings` and `TenantContext`.
    The insight engine reads thresholds via this protocol so it can be called
    with either object without changing the call site."""

    @property
    def bottleneck_time_ratio_threshold(self) -> float: ...
    @property
    def bottleneck_time_ratio_extra_threshold(self) -> float: ...
    @property
    def bottleneck_wip_ratio_threshold(self) -> float: ...
    @property
    def bottleneck_throughput_delta_threshold(self) -> float: ...
    @property
    def bottleneck_min_score(self) -> int: ...
    @property
    def trend_increase_threshold(self) -> float: ...
    @property
    def trend_decrease_threshold(self) -> float: ...
    @property
    def terminal_statuses(self) -> list[str]: ...
    @property
    def external_blocking_statuses(self) -> list[str]: ...


@dataclass
class StatusSignals:
    status: str
    time_ratio: float | None
    wip_ratio: float | None
    throughput_delta: float | None
    score: int
    reasons: list[str]


@dataclass
class BottleneckInsight:
    status: str
    score: int
    confidence: Confidence
    reasons: list[str]
    time_ratio: float | None
    wip_ratio: float | None
    throughput_delta: float | None
    current_avg_seconds: float
    previous_avg_seconds: float
    current_wip: float
    previous_wip: float
    current_throughput: int
    previous_throughput: int


@dataclass
class TrendChange:
    metric: str
    status: str | None
    current_value: float
    previous_value: float
    ratio: float
    change_pct: float
    direction: Literal["worsening", "improving", "stable"]


@dataclass
class InsightReport:
    window_start: datetime
    window_end: datetime
    previous_window_start: datetime
    previous_window_end: datetime
    bottleneck: BottleneckInsight | None
    candidates: list[StatusSignals] = field(default_factory=list)
    trends: list[TrendChange] = field(default_factory=list)


def _safe_ratio(current: float, previous: float) -> float | None:
    if previous <= 0:
        return None
    return current / previous


def _safe_delta(current: float, previous: float) -> float | None:
    if previous <= 0:
        return None
    return (current - previous) / previous


def _confidence_for(score: int) -> Confidence:
    if score >= 5:
        return "very_high"
    if score >= 4:
        return "high"
    return "medium"


def _score_status(
    current: StatusWindowResult,
    previous: StatusWindowResult,
    settings: _Thresholds,
) -> StatusSignals:
    """Apply the scoring model from 06_insight_engine_spec.md.

    score:
      time_ratio  >= 1.3  → +2
      wip_ratio   >= 1.2  → +1
      throughput_delta <= -0.2 → +1
      time_ratio  >= 1.5  → +1 (extra weight)
    """
    time_ratio = _safe_ratio(current.avg_seconds, previous.avg_seconds)
    wip_ratio = _safe_ratio(current.wip_avg, previous.wip_avg)
    throughput_delta = _safe_delta(current.throughput, previous.throughput)

    score = 0
    reasons: list[str] = []

    if time_ratio is not None and time_ratio >= settings.bottleneck_time_ratio_threshold:
        score += 2
        reasons.append(f"Average time increased {round((time_ratio - 1) * 100)}%")
    if wip_ratio is not None and wip_ratio >= settings.bottleneck_wip_ratio_threshold:
        score += 1
        reasons.append(f"WIP increased {round((wip_ratio - 1) * 100)}%")
    if (
        throughput_delta is not None
        and throughput_delta <= settings.bottleneck_throughput_delta_threshold
    ):
        score += 1
        reasons.append(f"Throughput decreased {round(abs(throughput_delta) * 100)}%")
    if time_ratio is not None and time_ratio >= settings.bottleneck_time_ratio_extra_threshold:
        score += 1

    return StatusSignals(
        status=current.status,
        time_ratio=time_ratio,
        wip_ratio=wip_ratio,
        throughput_delta=throughput_delta,
        score=score,
        reasons=reasons,
    )


def detect_bottleneck(
    current: WindowSnapshot,
    previous: WindowSnapshot,
    settings: _Thresholds,
) -> tuple[BottleneckInsight | None, list[StatusSignals]]:
    # Terminal statuses (Done, Won't Do, Cancelled, etc.) are workflow
    # endpoints — tickets accumulate there and time-in-status grows without
    # bound, so they ALWAYS look like an extreme bottleneck on the raw math
    # (avg_time ratio can be +90,000% as more tickets land). Same exclusion
    # `compute_trends` applies (case-folded match against per-tenant
    # `terminal_statuses`). Without this filter, "Done is the bottleneck"
    # was a routine misfire — see 2026-06-01 observation on example-tenant
    # (carried over to current versions before this fix).
    terminal_set = {s.casefold() for s in settings.terminal_statuses}
    # ADR-0042: external-blocking statuses (Blocked / Waiting on Customer /
    # etc.) are excluded from bottleneck attribution but still surfaced in
    # slice data and charts. The bottleneck card answers "where is the team's
    # *controllable* bottleneck?" — time the team can't act on should not
    # drive attribution. Same case-folded match as terminal_set.
    external_blocking_set = {s.casefold() for s in settings.external_blocking_statuses}
    candidates: list[StatusSignals] = []
    best: BottleneckInsight | None = None

    for status, cur in current.statuses.items():
        folded = status.casefold()
        if folded in terminal_set or folded in external_blocking_set:
            continue
        prev = previous.statuses.get(status)
        if prev is None:
            continue
        signals = _score_status(cur, prev, settings)
        candidates.append(signals)

        if signals.score < settings.bottleneck_min_score:
            continue

        insight = BottleneckInsight(
            status=status,
            score=signals.score,
            confidence=_confidence_for(signals.score),
            reasons=signals.reasons,
            time_ratio=signals.time_ratio,
            wip_ratio=signals.wip_ratio,
            throughput_delta=signals.throughput_delta,
            current_avg_seconds=cur.avg_seconds,
            previous_avg_seconds=prev.avg_seconds,
            current_wip=cur.wip_avg,
            previous_wip=prev.wip_avg,
            current_throughput=cur.throughput,
            previous_throughput=prev.throughput,
        )
        if best is None or insight.score > best.score:
            best = insight
        elif insight.score == best.score and insight.status < best.status:
            # Deterministic tie-break: alphabetical status name.
            best = insight

    candidates.sort(key=lambda s: (-s.score, s.status))
    return best, candidates


def compute_trends(
    current: WindowSnapshot,
    previous: WindowSnapshot,
    settings: _Thresholds,
    cycle_time_current: float | None = None,
    cycle_time_previous: float | None = None,
) -> list[TrendChange]:
    trends: list[TrendChange] = []

    # Meaningfulness thresholds (ux-fix-sprint item #6). Tiny baselines
    # produce nonsense ratios (5min→55min is +1000% but the absolute delta
    # is trivial). Suppress entries whose prior value is below these floors
    # so the trend list stays signal, not arithmetic artifacts.
    MIN_TIME_BASELINE_SECONDS = 1800.0  # 30 min
    MIN_COUNT_BASELINE = 2.0  # WIP averages below 2 issues aren't meaningful

    # Terminal statuses (Done, Won't Do, Cancelled, etc.) are workflow
    # endpoints — tickets accumulate there, so avg time-in-status isn't a
    # meaningful flow metric. Same exclusion the CFD applies.
    terminal_set = {s.casefold() for s in settings.terminal_statuses}

    def _classify(
        ratio: float, *, lower_is_better: bool
    ) -> Literal["worsening", "improving", "stable"]:
        if ratio >= settings.trend_increase_threshold:
            return "worsening" if lower_is_better else "improving"
        if ratio <= settings.trend_decrease_threshold:
            return "improving" if lower_is_better else "worsening"
        return "stable"

    if (
        cycle_time_current is not None
        and cycle_time_previous is not None
        and cycle_time_previous >= MIN_TIME_BASELINE_SECONDS
    ):
        ratio = cycle_time_current / cycle_time_previous
        trends.append(
            TrendChange(
                metric="cycle_time",
                status=None,
                current_value=cycle_time_current,
                previous_value=cycle_time_previous,
                ratio=ratio,
                change_pct=(ratio - 1) * 100,
                direction=_classify(ratio, lower_is_better=True),
            )
        )

    # Per-status trends require enough prior-window data to be meaningful.
    # A prior window with one or two issues passing through a status produces
    # ratios like +33,701,452% on the first reasonable current week — math
    # artifact, not signal. Three is the smallest sample where the average
    # starts to be defensible.
    MIN_PRIOR_SAMPLE = 3

    for status, cur in current.statuses.items():
        if status.casefold() in terminal_set:
            continue
        prev = previous.statuses.get(status)
        if prev is None or prev.sample_size < MIN_PRIOR_SAMPLE:
            continue
        if prev.avg_seconds >= MIN_TIME_BASELINE_SECONDS:
            r = cur.avg_seconds / prev.avg_seconds
            trends.append(
                TrendChange(
                    metric="avg_time",
                    status=status,
                    current_value=cur.avg_seconds,
                    previous_value=prev.avg_seconds,
                    ratio=r,
                    change_pct=(r - 1) * 100,
                    direction=_classify(r, lower_is_better=True),
                )
            )
        if prev.wip_avg >= MIN_COUNT_BASELINE:
            r = cur.wip_avg / prev.wip_avg
            trends.append(
                TrendChange(
                    metric="wip",
                    status=status,
                    current_value=cur.wip_avg,
                    previous_value=prev.wip_avg,
                    ratio=r,
                    change_pct=(r - 1) * 100,
                    direction=_classify(r, lower_is_better=True),
                )
            )
        if prev.throughput >= MIN_PRIOR_SAMPLE:
            # Throughput is a count, not an average; require the same minimum
            # prior count so a 1-issue prior week doesn't produce 9900%.
            r = cur.throughput / prev.throughput
            trends.append(
                TrendChange(
                    metric="throughput",
                    status=status,
                    current_value=float(cur.throughput),
                    previous_value=float(prev.throughput),
                    ratio=r,
                    change_pct=(r - 1) * 100,
                    direction=_classify(r, lower_is_better=False),
                )
            )

    # Show most-changed first; deterministic tiebreak by metric/status.
    trends.sort(key=lambda t: (-abs(t.ratio - 1.0), t.metric, t.status or ""))
    return trends


def generate_insight_report(
    current: WindowSnapshot,
    previous: WindowSnapshot,
    settings: _Thresholds,
    cycle_time_current: float | None = None,
    cycle_time_previous: float | None = None,
) -> InsightReport:
    bottleneck, candidates = detect_bottleneck(current, previous, settings)
    trends = compute_trends(
        current,
        previous,
        settings,
        cycle_time_current=cycle_time_current,
        cycle_time_previous=cycle_time_previous,
    )
    return InsightReport(
        window_start=current.window_start,
        window_end=current.window_end,
        previous_window_start=previous.window_start,
        previous_window_end=previous.window_end,
        bottleneck=bottleneck,
        candidates=candidates,
        trends=trends,
    )
