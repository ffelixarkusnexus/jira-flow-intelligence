"""Demo seed — convincing synthetic dataset designed for Marketplace
screenshots, sales demos, and "check this dashboard out" moments.

Generates 250 issues over 60 days, 5 sprints (4 closed + 1 active),
5 assignees, varied issue types/priorities/story points, with a
deliberate Review-stage bottleneck in the current window that
triggers all five alert types simultaneously:

- ``status_duration`` — 8+ issues stuck in Review > 48h.
- ``no_activity``     — 3 issues in Review > 72h with no transitions.
- ``cycle_time``      — 6 Done issues in last 14d with cycle > 7d.
- ``trend``           — average cycle rises ~3d (Sprint 1) → ~6d (Sprint 4),
                        well past the 20% worsening threshold.
- ``wip_breach``      — Review WIP = 24 against limit of 15.

Runs deterministically (seeded with `random.Random(42)`); same input
produces the same dataset every time, so screenshots are reproducible.

Run as a CLI against a local dev backend:
    uv run python -m app.seeds.demo
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.tenant_context import TenantContext
from app.db.models import Tenant, WipLimit, init_db
from app.db.session import db_session
from app.services.alert_service import upsert_rule
from app.services.ingestion_service import process_payloads
from app.services.metrics_service import recompute_all_issue_metrics

DEMO_TENANT_KEY = "demo-tenant"
STATUS_FLOW = ["Todo", "In Progress", "Review", "Done"]

# Default project key for the synthetic dataset. Overridable per-call so
# the dev-only `/api/dev/seed-demo` endpoint can populate against the
# project the caller is actually viewing — the dashboard is project-
# scoped (`jira:projectPage`) and filters every chart query by
# project_key. Without an override, demo issues would be tagged with
# "DEMO" and become invisible from any real Jira project page.
DEFAULT_DEMO_PROJECT_KEY = "DEMO"

# Sprint custom field. Jira Cloud's default Sprint field id is
# `customfield_10020` (since 2018); the ingestion layer probes a static
# list and falls back to a heuristic, so any of the standard ids would
# work. We pin to 10020 so the dashboard's sprint detection lands on
# the obvious answer with no probing.
SPRINT_CUSTOM_FIELD = "customfield_10020"

# 5 distinct synthetic assignees with weighted distribution so charts
# don't look uniformly distributed (which reads as fake). Alice and
# Bob carry the most work; Charlie/Dana are mid; Eve is part-time.
_ASSIGNEES: list[tuple[str, str]] = [
    ("alice.chen", "Alice Chen"),
    ("bob.wright", "Bob Wright"),
    ("charlie.park", "Charlie Park"),
    ("dana.ortiz", "Dana Ortiz"),
    ("eve.kumar", "Eve Kumar"),
]
_ASSIGNEE_WEIGHTS = [0.30, 0.25, 0.20, 0.15, 0.10]

# Realistic engineering-flavored summaries. ~80 templates so the same
# string never appears twice across 250 issues. Mixed scope (features,
# bugs, infra, docs) so it reads like a real backlog.
_SUMMARIES = [
    "Add filter for completed issues on dashboard",
    "Fix login redirect loop on Safari 17",
    "Optimize SQL query in issue search",
    "Refactor authentication middleware",
    "Update user profile settings UI",
    "Migrate webhook handler to async pattern",
    "Add export-to-CSV for cycle time report",
    "Fix race condition in concurrent sync",
    "Investigate intermittent 500s on the dashboard load",
    "Implement WIP limit breach notifications",
    "Add support for nested issue links",
    "Reduce bundle size on dashboard frontend",
    "Document the new sprint-bucketed window picker",
    "Backport sprint detection fix to v2 branch",
    "Triage incoming Sentry errors from yesterday",
    "Update Tailwind to v4 in the docs site",
    "Wire up DKIM for outbound email signing",
    "Remove dead app_key field from backend config",
    "Fix off-by-one in P95 cycle time calculation",
    "Add coverage to the rate limiter middleware",
    "Investigate slow query on tenants table",
    "Re-enable unit tests after CDK migration",
    "Document the lifecycleResolver upgrade path",
    "Reset stuck backfill job for tenant X",
    "Patch CVE-2026-1234 in transitive dep",
    "Improve error message on stale FIT rejection",
    "Add structured logging for sync events",
    "Move the alerts API behind feature flag",
    "Wire up the docs site analytics",
    "Add iOS apple-touch-icon to web app",
    "Standardize timestamps to UTC across backend",
    "Audit secrets manager rotation policies",
    "Fix flaky test in ingestion suite",
    "Index issues by tenant_id for query speed",
    "Document the cross-stack ref pattern",
    "Add health endpoint for App Runner probes",
    "Investigate increased webhook ingest latency",
    "Reduce CFD recompute cost via caching",
    "Add 404 page to docs site",
    "Validate ADF input on description fields",
    "Migrate from Connect descriptor to Forge",
    "Add audit trail for settings changes",
    "Support additional sprint-state filters",
    "Backport WIP limit breach to existing tenants",
    "Add a 'paused' state to alert rule schema",
    "Fix the off-by-one when computing throughput",
    "Improve onboarding empty-state copy",
    "Add a 'last activity' indicator on issue card",
    "Support custom-field detection by name pattern",
    "Add e2e test for the dashboard load path",
    "Investigate spike in log-emit-error metric",
    "Audit dependency tree for CVE alerts",
    "Add explanation tooltip for trend signal",
    "Standardize the alerting threshold UX",
    "Decouple sync from rendering cycle",
    "Move heavy aggregation to background job",
    "Implement a token bucket for incoming syncs",
    "Test the disaster recovery runbook end-to-end",
    "Add operator dashboard for support tickets",
    "Revisit the cron schedule for daily reconcile",
    "Validate the ACM cert renewal pathway",
    "Document the seed-demo gate (allow_demo_seed)",
    "Add a button to re-trigger backfill",
    "Fix the regex for sprint custom field discovery",
    "Add ARIA labels to all chart elements",
    "Reproduce the missing-FIT scenario in tests",
    "Add CI test for the Postgres RLS policy",
    "Build the v1.1 release packaging script",
    "Re-evaluate the bottleneck scoring formula",
    "Cache JWKS results for 24h instead of 1h",
    "Add a legend to the cycle time scatter",
    "Make the dashboard responsive below 1024px",
    "Investigate the NaN bug in throughput delta",
    "Migrate Anthropic SDK to v0.45+",
    "Add a developer setup script for new contributors",
    "Document the data residency story for EU customers",
    "Investigate the 11s render time on first visit",
    "Add tooltip to WIP Aging x-axis label",
    "Reduce the number of CloudWatch metric emits",
    "Add the rest-of-board view to the navigation",
    "Audit the sub-processor list for completeness",
    "Document the upgrade path for example-tenant",
]

_ISSUE_TYPES = [
    ("Story", 0.55),
    ("Task", 0.25),
    ("Bug", 0.15),
    ("Epic", 0.05),
]

_PRIORITIES = [
    ("Lowest", 0.05),
    ("Low", 0.15),
    ("Medium", 0.50),
    ("High", 0.25),
    ("Highest", 0.05),
]

# Fibonacci-weighted toward 3/5/8 (the realistic estimates that
# experienced Scrum teams actually use). 1 and 13 appear but rarely.
_STORY_POINTS = [
    (1, 0.10),
    (2, 0.15),
    (3, 0.30),
    (5, 0.25),
    (8, 0.15),
    (13, 0.05),
]


def _weighted(rng: random.Random, choices: list[tuple[Any, float]]) -> Any:
    items, weights = zip(*choices, strict=False)
    return rng.choices(items, weights=weights, k=1)[0]


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


# --- Sprint scaffolding ---------------------------------------------------
#
# 5 sprints, 14 days each. Sprints 1-4 closed; Sprint 5 active. Dates
# anchor on `now` so the fixture is "today-relative" — re-running
# tomorrow produces a dataset that's still 60 days deep ending today.

_SPRINT_DEFS = [
    # (offset_id, name, days_ago_start, days_ago_end, state)
    (5001, "Sprint 1 — Foundations", 60, 46, "closed"),
    (5002, "Sprint 2 — Onboarding", 45, 31, "closed"),
    (5003, "Sprint 3 — Charts", 30, 16, "closed"),
    (5004, "Sprint 4 — Alerts & Limits", 15, 1, "closed"),
    (5005, "Sprint 5 — Marketplace push", 0, -14, "active"),  # negative end = future
]


def _sprint_payload(
    sprint_def: tuple[int, str, int, int, str],
    now: datetime,
    project_key: str,
) -> dict[str, Any]:
    sid, name, start_days_ago, end_days_ago, state = sprint_def
    start_at = now - timedelta(days=start_days_ago)
    end_at = now - timedelta(days=end_days_ago)
    payload: dict[str, Any] = {
        "id": sid,
        "name": name,
        "state": state,
        "boardId": 1,
        "startDate": _iso(start_at),
        "endDate": _iso(end_at),
    }
    if state == "closed":
        payload["completeDate"] = _iso(end_at)
    return payload


# --- Issue construction --------------------------------------------------


def _build_transitions(
    created_at: datetime,
    durations_hours: dict[str, float],
    *,
    completes: bool,
) -> list[dict[str, Any]]:
    """Walk through STATUS_FLOW emitting changelog entries with the
    durations supplied per source-status. Stops before "Done" if
    ``completes=False``, leaving the issue in whatever status the last
    populated duration ended in."""
    transitions: list[dict[str, Any]] = []
    cur = created_at
    for i in range(len(STATUS_FLOW) - 1):
        from_status = STATUS_FLOW[i]
        to_status = STATUS_FLOW[i + 1]
        if from_status not in durations_hours:
            break
        cur = cur + timedelta(hours=durations_hours[from_status])
        if not completes and to_status == "Done":
            break
        transitions.append(
            {
                "created": _iso(cur),
                "items": [
                    {
                        "field": "status",
                        "fromString": from_status,
                        "toString": to_status,
                    }
                ],
            }
        )
    return transitions


def _build_issue(
    rng: random.Random,
    *,
    key: str,
    summary: str,
    created_at: datetime,
    durations_hours: dict[str, float],
    completes: bool,
    project_key: str,
    sprint_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    transitions = _build_transitions(created_at, durations_hours, completes=completes)
    last_status = transitions[-1]["items"][0]["toString"] if transitions else "Todo"
    resolution = transitions[-1]["created"] if completes and last_status == "Done" else None

    issue_type, _ = _weighted(rng, _ISSUE_TYPES), None
    priority = _weighted(rng, _PRIORITIES)
    story_points = _weighted(rng, _STORY_POINTS)
    assignee_idx = rng.choices(range(len(_ASSIGNEES)), weights=_ASSIGNEE_WEIGHTS, k=1)[0]
    assignee_id, assignee_name = _ASSIGNEES[assignee_idx]

    fields: dict[str, Any] = {
        "created": _iso(created_at),
        "updated": transitions[-1]["created"] if transitions else _iso(created_at),
        "status": {"name": last_status},
        "issuetype": {"name": issue_type},
        "priority": {"name": priority},
        "project": {"key": project_key},
        "summary": summary,
        "resolutiondate": resolution,
        "assignee": {
            "accountId": assignee_id,
            "displayName": assignee_name,
            "emailAddress": f"{assignee_id}@example.com",
        },
        # Story Points custom field — Jira Cloud default
        # `customfield_10016`. Non-load-bearing for any chart today,
        # but readers expect to see SP populated on real Jira data.
        "customfield_10016": story_points,
    }
    if sprint_payload is not None:
        fields[SPRINT_CUSTOM_FIELD] = [sprint_payload]

    return {
        "id": key,
        "key": key,
        "fields": fields,
        "changelog": {"histories": transitions},
    }


# --- Cohort builders -----------------------------------------------------


def _summary_pool(rng: random.Random, count: int) -> list[str]:
    """Sample `count` summaries with replacement only after exhausting
    the pool, so the first 80 issues get unique summaries and any
    above-80 see at most one repeat per template."""
    pool = _SUMMARIES.copy()
    rng.shuffle(pool)
    out: list[str] = []
    while len(out) < count:
        out.extend(pool)
    return out[:count]


def _hours_for_sprint(rng: random.Random, sprint_index: int) -> dict[str, float]:
    """Sprint 1-2 baseline (Review ~5h), Sprint 3 mild creep (~7h),
    Sprint 4 worse (~10h). Drives the trend signal: avg cycle time
    rises ~50% from start to end, well past the 20% threshold."""
    todo = rng.uniform(2, 8)
    in_progress = rng.uniform(8, 16)
    if sprint_index <= 2:
        review = rng.uniform(3, 6)
    elif sprint_index == 3:
        review = rng.uniform(5, 9)
    else:  # sprint 4
        review = rng.uniform(8, 14)
    return {"Todo": todo, "In Progress": in_progress, "Review": review}


def _build_closed_sprint_issues(
    rng: random.Random,
    sprint_index: int,
    sprint_def: tuple[int, str, int, int, str],
    sprint_payload: dict[str, Any],
    issue_count: int,
    long_cycle_count: int,
    project_key: str,
    now: datetime,
    summary_iter: list[str],
    key_prefix: str,
) -> list[dict[str, Any]]:
    """Build N completed issues attributed to a closed sprint.

    `long_cycle_count` of them get extreme review durations (40-80h)
    so they cycle in 8-14 days — these are the issues the cycle_time
    > 7d alert will fire on. Concentrated in Sprint 4 to drive the
    "current window" cycle alert."""
    _, _, start_days_ago, end_days_ago, _ = sprint_def
    sprint_start = now - timedelta(days=start_days_ago)
    sprint_end = now - timedelta(days=end_days_ago)
    # `start_days_ago > end_days_ago` always (we count days-ago descending),
    # so sprint_start is the EARLIER timestamp and sprint_end is the LATER
    # one. Sprint duration is therefore (sprint_end - sprint_start), not
    # the other way around. An earlier version flipped the subtraction,
    # which collapsed every sprint's issue creation to a single instant
    # and produced visible clumping on CFD/Cycle-Scatter screenshots.
    sprint_duration_s = max(1, (sprint_end - sprint_start).total_seconds())

    issues: list[dict[str, Any]] = []
    for i in range(issue_count):
        # Spread issue creation evenly across the sprint window.
        offset_s = rng.uniform(0, sprint_duration_s)
        created_at = sprint_start + timedelta(seconds=offset_s)

        durations = _hours_for_sprint(rng, sprint_index)
        if i < long_cycle_count:
            # Force a > 7d cycle by bloating the Review time
            durations["Review"] = rng.uniform(40, 80) * 3
        issues.append(
            _build_issue(
                rng,
                key=f"{key_prefix}-{i + 1:03d}",
                summary=summary_iter.pop(),
                created_at=created_at,
                durations_hours=durations,
                completes=True,
                project_key=project_key,
                sprint_payload=sprint_payload,
            )
        )
    return issues


def _build_active_sprint_issues(
    rng: random.Random,
    sprint_payload: dict[str, Any],
    project_key: str,
    now: datetime,
    summary_iter: list[str],
) -> list[dict[str, Any]]:
    """The active-sprint cohort that drives the Overview bottleneck:

      - 8 in Todo (0-3 days old, freshly created)
      - 18 in In Progress (1-7 days)
      - 24 in Review:
          * 13 normal (1-3 days in Review)
          * 8 stuck (5-7 days in Review — fires status_duration alert)
          * 3 abandoned (8-12 days in Review with no recent activity —
            fires no_activity alert)
      - 0 Done (sprint just started)

    Total: 50 in flight. Review WIP = 24, exceeds the limit of 15
    we'll seed via _seed_wip_limits → wip_breach alert fires too.
    """
    issues: list[dict[str, Any]] = []
    sprint_start = now - timedelta(days=1)

    # Todo: just-created tickets, 0-3 days old, no transitions yet
    for i in range(8):
        created_at = now - timedelta(hours=rng.uniform(2, 72))
        issues.append(
            _build_issue(
                rng,
                key=f"DEMO-A-T{i + 1:02d}",
                summary=summary_iter.pop(),
                created_at=created_at,
                durations_hours={},  # no transitions
                completes=False,
                project_key=project_key,
                sprint_payload=sprint_payload,
            )
        )

    # In Progress: 1-7 days old, 1 transition (Todo → In Progress)
    for i in range(18):
        created_at = now - timedelta(days=rng.uniform(1, 7))
        issues.append(
            _build_issue(
                rng,
                key=f"DEMO-A-P{i + 1:02d}",
                summary=summary_iter.pop(),
                created_at=created_at,
                durations_hours={"Todo": rng.uniform(2, 12)},
                completes=False,
                project_key=project_key,
                sprint_payload=sprint_payload,
            )
        )

    # Review (normal): 1-3 days in Review
    for i in range(13):
        created_at = sprint_start - timedelta(days=rng.uniform(1, 4))
        issues.append(
            _build_issue(
                rng,
                key=f"DEMO-A-R{i + 1:02d}",
                summary=summary_iter.pop(),
                created_at=created_at,
                durations_hours={
                    "Todo": rng.uniform(2, 8),
                    "In Progress": rng.uniform(8, 32),
                },
                completes=False,
                project_key=project_key,
                sprint_payload=sprint_payload,
            )
        )

    # Review (stuck): 5-7 days in Review (status_duration > 48h fires)
    for i in range(8):
        created_at = sprint_start - timedelta(days=rng.uniform(5, 7))
        issues.append(
            _build_issue(
                rng,
                key=f"DEMO-A-RS{i + 1:02d}",
                summary=summary_iter.pop(),
                created_at=created_at,
                durations_hours={
                    "Todo": rng.uniform(2, 8),
                    "In Progress": rng.uniform(8, 32),
                },
                completes=False,
                project_key=project_key,
                sprint_payload=sprint_payload,
            )
        )

    # Review (abandoned): 8-12 days in Review (no_activity > 72h fires
    # because the most recent transition is 8+ days old)
    for i in range(3):
        created_at = sprint_start - timedelta(days=rng.uniform(8, 12))
        issues.append(
            _build_issue(
                rng,
                key=f"DEMO-A-RZ{i + 1:02d}",
                summary=summary_iter.pop(),
                created_at=created_at,
                durations_hours={
                    "Todo": rng.uniform(2, 8),
                    "In Progress": rng.uniform(8, 32),
                },
                completes=False,
                project_key=project_key,
                sprint_payload=sprint_payload,
            )
        )

    return issues


def build_demo_payloads(
    now: datetime | None = None,
    *,
    project_key: str = DEFAULT_DEMO_PROJECT_KEY,
) -> list[dict[str, Any]]:
    """Generate the full 250-issue dataset.

    Layout:
      - Sprint 1 (60-46d ago): 50 issues, all Done, healthy (~3d cycle).
      - Sprint 2 (45-31d ago): 55 issues, all Done, healthy (~3.5d).
      - Sprint 3 (30-16d ago): 50 issues, all Done, mild creep (~4-5d).
      - Sprint 4 (15-1d ago):  45 issues, all Done, 6 with > 7d cycle.
      - Sprint 5 (active):     50 issues mid-flight; 24 in Review,
                               of which 8 stuck > 5d, 3 abandoned > 8d.

    Total: 250 issues, 200 Done, 50 in flight.
    """
    rng = random.Random(42)
    now = now or utcnow()
    summaries = _summary_pool(rng, 260)  # buffer above 250 for safety

    payloads: list[dict[str, Any]] = []

    closed_sprint_specs = [
        # (sprint_index, count, long_cycle_count, key_prefix)
        (1, 50, 0, "DEMO-S1"),
        (2, 55, 0, "DEMO-S2"),
        (3, 50, 0, "DEMO-S3"),
        (4, 45, 6, "DEMO-S4"),  # 6 issues with > 7d cycle drive cycle_time alert
    ]
    for sprint_index, count, long_cycles, prefix in closed_sprint_specs:
        sprint_def = _SPRINT_DEFS[sprint_index - 1]
        sprint_payload = _sprint_payload(sprint_def, now, project_key)
        payloads.extend(
            _build_closed_sprint_issues(
                rng,
                sprint_index=sprint_index,
                sprint_def=sprint_def,
                sprint_payload=sprint_payload,
                issue_count=count,
                long_cycle_count=long_cycles,
                project_key=project_key,
                now=now,
                summary_iter=summaries,
                key_prefix=prefix,
            )
        )

    # Active sprint
    active_def = _SPRINT_DEFS[4]
    active_payload = _sprint_payload(active_def, now, project_key)
    payloads.extend(
        _build_active_sprint_issues(
            rng,
            sprint_payload=active_payload,
            project_key=project_key,
            now=now,
            summary_iter=summaries,
        )
    )

    return payloads


# --- Tenant + ancillary seeding ------------------------------------------


def upsert_demo_tenant(session: Session) -> Tenant:
    tenant = session.get(Tenant, DEMO_TENANT_KEY)
    if tenant is None:
        tenant = Tenant(client_key=DEMO_TENANT_KEY)
        session.add(tenant)
    tenant.cloud_id = "demo-cloud-id"
    tenant.base_url = "https://demo.atlassian.net"
    tenant.display_url = "https://demo.atlassian.net"
    tenant.product_type = "jira"
    tenant.forge_installation_id = DEMO_TENANT_KEY
    tenant.plan = "free"
    tenant.enabled = True
    tenant.installed_at = utcnow()
    session.flush()
    return tenant


def seed_alert_rules(session: Session, tenant_id: str) -> None:
    """The 5 default alert rules every demo install gets. Designed so
    each one fires against the synthetic data in `build_demo_payloads`
    — so every fresh demo dashboard has at least one alert visible
    per type."""
    upsert_rule(
        session,
        tenant_id,
        "review-stuck-48h",
        "status_duration",
        {"status": "Review", "threshold_seconds": 48 * 3600},
    )
    upsert_rule(
        session,
        tenant_id,
        "cycle-7d",
        "cycle_time",
        {"threshold_seconds": 7 * 86400},
    )
    upsert_rule(
        session,
        tenant_id,
        "no-activity-72h",
        "no_activity",
        {"threshold_seconds": 72 * 3600},
    )
    upsert_rule(
        session,
        tenant_id,
        "trend-cycle-worsening",
        "trend",
        {"metric": "cycle_time", "direction": "worsening", "threshold_pct": 20.0},
    )
    upsert_rule(
        session,
        tenant_id,
        "review-wip-breach",
        "wip_breach",
        # No project_key restriction → applies tenant-wide. The matching
        # WipLimit row (seeded below) controls the actual cap value.
        {"status": "Review"},
    )


def seed_wip_limits(
    session: Session,
    tenant_id: str,
    project_key: str = DEFAULT_DEMO_PROJECT_KEY,
) -> None:
    """One WIP limit per status that the dashboard cares about. Review
    is set deliberately tight (15) so the active-sprint cohort's 24
    Review-status issues breach it — driving the wip_breach alert and
    the in-app breach indicator. Other limits sit comfortably above
    expected WIP so they DON'T fire and the bottleneck reads cleanly
    as "Review only".

    `breach_minutes > 0` is required for the wip_breach alert to fire
    (per ADR-0022). The 30-minute setting we use here means "a breach
    must persist for at least 30 minutes before alerting" — easily
    satisfied by the synthetic dataset where breaches are days old.
    """
    now = utcnow()
    limits: list[tuple[str, int]] = [
        ("In Progress", 30),  # 18 active, plenty of headroom
        ("Review", 15),  # 24 active → 60% breach
    ]
    for status, max_in_progress in limits:
        existing = (
            session.query(WipLimit)
            .filter(
                WipLimit.tenant_id == tenant_id,
                WipLimit.project_key == project_key,
                WipLimit.status == status,
            )
            .one_or_none()
        )
        if existing is None:
            session.add(
                WipLimit(
                    tenant_id=tenant_id,
                    project_key=project_key,
                    status=status,
                    max_in_progress=max_in_progress,
                    breach_minutes=30,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            existing.max_in_progress = max_in_progress
            existing.breach_minutes = 30
            existing.updated_at = now
    session.flush()


def seed_demo_data_for_tenant(
    session: Session,
    tenant: Tenant,
    settings,  # type: ignore[no-untyped-def]
    *,
    project_key: str = DEFAULT_DEMO_PROJECT_KEY,
) -> object:
    """Seed the synthetic dataset against an arbitrary tenant.

    Used by the dev-only `/api/dev/seed-demo` endpoint so a Forge install
    can populate its own tenant with the bottleneck-shaped fixture
    without waiting on real Jira data.

    `project_key` controls which Jira project the synthetic issues are
    bucketed under. The dashboard is project-scoped (`jira:projectPage`)
    and filters every chart query by project_key, so the seed must use
    the project the caller is currently viewing — otherwise the seed
    succeeds but no data renders. The default ("DEMO") preserves the
    fully-synthetic-tenant use case (`uv run python -m app.seeds.demo`).

    Wipes all existing issues + transitions + slices for the tenant
    first so charts aren't polluted by any real-Jira data already
    ingested. Cascade is via FK ON DELETE CASCADE on the issue rows;
    alert rules + WIP limits are upserted (preserved + topped up).
    """
    from app.db.models import Issue

    session.query(Issue).filter(Issue.tenant_id == tenant.client_key).delete(
        synchronize_session=False
    )
    session.flush()

    payloads = build_demo_payloads(project_key=project_key)
    ctx = TenantContext(tenant=tenant, settings=settings)
    report = process_payloads(session, payloads, ctx)
    recompute_all_issue_metrics(session, ctx)
    seed_alert_rules(session, ctx.tenant_id)
    seed_wip_limits(session, ctx.tenant_id, project_key=project_key)
    return report


def main() -> None:
    init_db()
    settings = get_settings()
    with db_session() as session:
        tenant = upsert_demo_tenant(session)
        report = seed_demo_data_for_tenant(session, tenant, settings)
    print(
        f"Seeded: tenant={DEMO_TENANT_KEY}, issues={report.issues_processed}, "  # type: ignore[attr-defined]
        f"transitions={report.transitions_written}, "  # type: ignore[attr-defined]
        f"slices={report.slices_written}, errors={len(report.errors)}"  # type: ignore[attr-defined]
    )


if __name__ == "__main__":
    main()
