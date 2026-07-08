"""Forge-side Jira sync.

The Forge resolver fetches issues from Atlassian's REST API using the
install's `read:jira-work` scope (`@forge/api`'s `requestJira`), then
POSTs the raw payloads here. We process them through the same ingestion
pipeline the seeded demo uses — `process_payloads` — and recompute
metrics for ONLY the issue IDs that just came in, so the sync
cost scales with delta size rather than total tenant size.

Per ADR-0019, the backend has no Jira credentials of its own. Doing
the fetch in Forge keeps customer data flowing through their own
install scopes (no API tokens, no global env vars), which is the
multi-tenant clean answer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from dateutil import parser as dtparser
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.deps import current_tenant_context
from app.core.logging import get_logger
from app.core.tenant_context import TenantContext
from app.db.models import Issue, Tenant
from app.db.session import get_db
from app.services.alert_dispatch import dispatch_alerts
from app.services.alert_service import evaluate_issue_alerts
from app.services.ingestion_service import process_payloads
from app.services.metrics_service import recompute_issue_metrics_for
from app.services.resend_service import fire_terminal_state_email
from app.services.sprint_service import set_issue_sprints, upsert_sprint

logger = get_logger(__name__)

router = APIRouter(prefix="/forge/sync", tags=["forge-sync"])


class IngestRequest(BaseModel):
    """Body shape from the Forge resolver — a passthrough of the Atlassian
    Search API issues array (each entry has `id`, `key`, `fields`, and
    `changelog.histories`).

    `skip_if_stale`: when True, payloads whose `fields.updated` is
    not newer than the existing row's `updated_at` are skipped. The
    webhook resolver sets this to defend against duplicate / out-of-order
    deliveries; bulk Sync paths leave it False so Force-full can re-process.
    """

    payloads: list[dict[str, Any]]
    skip_if_stale: bool = False
    # ADR-0037 entry point 2: when True, run targeted per-issue alert evaluation
    # for the ingested issues (status_duration / cycle_time). The live issue
    # webhook sets this to tighten detection latency; bulk paths (backfill,
    # reconcile) leave it False so historical re-ingestion doesn't burst-evaluate
    # — the daily/hourly sweep covers those.
    run_alert_eval: bool = False


@router.get("/state", status_code=200)
def state(
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> dict[str, Any]:
    """Tenant's last successful sync timestamp + backfill state.

    The resolver reads `lastSyncedAt` on every Sync click: if set, the
    next Jira fetch is bounded by it (`updated >= <lastSyncedAt>`) so
    subsequent syncs only pick up changes rather than refetching the
    full 30-day window. The backfill block tells the UI whether a
    historical-data pull is in progress so it can render a progress bar.
    """
    tenant = db.execute(select(Tenant).where(Tenant.client_key == ctx.tenant_id)).scalar_one()
    return {
        "lastSyncedAt": tenant.last_sync_at.isoformat() if tenant.last_sync_at else None,
        "backfill": {
            "status": tenant.backfill_status,  # None | pending | running | completed | failed
            "totalIssues": tenant.backfill_total_issues,
            "processedIssues": tenant.backfill_processed_issues,
            "startedAt": tenant.backfill_started_at.isoformat()
            if tenant.backfill_started_at
            else None,
            "completedAt": tenant.backfill_completed_at.isoformat()
            if tenant.backfill_completed_at
            else None,
            # ADR-0033 outcome #4: the Custom UI uses `acknowledgedAt` to
            # decide whether to render the dashboard completion banner —
            # null → show banner; non-null → suppress permanently.
            "acknowledgedAt": tenant.backfill_acknowledged_at.isoformat()
            if tenant.backfill_acknowledged_at
            else None,
            "error": tenant.backfill_error,
        },
        # ADR-0033 active-push destination. Null means we haven't captured
        # one yet; the Settings UI surfaces a prompt to add one when null.
        "adminContactEmail": tenant.admin_contact_email,
    }


class BackfillStartRequest(BaseModel):
    """The Forge consumer calls this once at the start of a backfill run
    to flip the tenant's status to `running`. Subsequent ingest calls
    update progress fields. Idempotent: repeated calls keep status running."""

    total_estimate: int | None = None  # if Jira's `total` is reliable; else None


@router.post("/backfill/start", status_code=200)
def backfill_start(
    body: BackfillStartRequest,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> dict[str, Any]:
    tenant = db.execute(select(Tenant).where(Tenant.client_key == ctx.tenant_id)).scalar_one()
    tenant.backfill_status = "running"
    tenant.backfill_total_issues = body.total_estimate
    tenant.backfill_processed_issues = 0
    tenant.backfill_started_at = utcnow()
    tenant.backfill_completed_at = None
    tenant.backfill_next_page_token = None
    tenant.backfill_error = None
    db.commit()
    logger.info(
        "Forge backfill started for tenant=%s estimate=%s",
        ctx.tenant_id,
        body.total_estimate,
    )
    return {"status": "running"}


class BackfillProgressRequest(BaseModel):
    processed_delta: int = 0
    next_page_token: str | None = None
    done: bool = False
    error: str | None = None


@router.post("/backfill/progress", status_code=200)
def backfill_progress(
    body: BackfillProgressRequest,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> dict[str, Any]:
    """Consumer reports progress per batch. Done=True flips status to
    completed; non-empty error flips to failed.

    ADR-0033 proactive-notification (CLAUDE.md rule #9): on the
    transition from a non-terminal status (None / pending / running) to
    a terminal status (completed / failed), fire the appropriate SES
    email to the tenant admin. We capture the OLD status before
    mutation so a re-invocation (Forge retrying the consumer after a
    transient failure) that re-flips an already-terminal state doesn't
    fire a duplicate email — the email fires once, on the first crossing
    of the boundary."""
    tenant = db.execute(select(Tenant).where(Tenant.client_key == ctx.tenant_id)).scalar_one()
    old_status = tenant.backfill_status
    tenant.backfill_processed_issues = (
        tenant.backfill_processed_issues or 0
    ) + body.processed_delta
    tenant.backfill_next_page_token = body.next_page_token
    if body.error:
        tenant.backfill_status = "failed"
        tenant.backfill_error = body.error
        tenant.backfill_completed_at = utcnow()
    elif body.done:
        tenant.backfill_status = "completed"
        tenant.backfill_completed_at = utcnow()
    db.commit()

    # Fire the proactive-notification email if we just crossed into a
    # terminal state. Email service swallows its own errors (sandbox not
    # lifted, identity unverified, etc.) and logs — never raises, so it
    # cannot rollback the state-transition above.
    if old_status not in ("completed", "failed") and tenant.backfill_status in (
        "completed",
        "failed",
    ):
        fire_terminal_state_email(tenant, db=db)

    return {"status": tenant.backfill_status}


@router.post("/backfill/acknowledge", status_code=200)
def backfill_acknowledge(
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> dict[str, Any]:
    """ADR-0033: customer dismissed the dashboard completion banner.

    No request body — POSTing the endpoint at all is the signal.
    Idempotent: repeated dismissals overwrite the timestamp without
    side-effects. Front-end uses the `acknowledgedAt` field on /state to
    decide whether to render the banner; once non-null the banner stays
    suppressed permanently for this backfill cycle."""
    tenant = db.execute(select(Tenant).where(Tenant.client_key == ctx.tenant_id)).scalar_one()
    tenant.backfill_acknowledged_at = utcnow()
    db.commit()
    return {"acknowledgedAt": tenant.backfill_acknowledged_at.isoformat()}


class AdminEmailRequest(BaseModel):
    """ADR-0033 admin-contact-email setter. Null clears the field; an
    empty string is treated as null too. The frontend allows the customer
    to opt out of notifications by clearing the field — this honors the
    customer-copy doc's unsubscribe path (Settings → clear email)."""

    email: str | None = None


@router.put("/admin-email", status_code=200)
def set_admin_email(
    body: AdminEmailRequest,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> dict[str, Any]:
    tenant = db.execute(select(Tenant).where(Tenant.client_key == ctx.tenant_id)).scalar_one()
    raw = (body.email or "").strip()
    tenant.admin_contact_email = raw if raw else None
    db.commit()
    logger.info(
        "Admin contact email %s for tenant=%s",
        "set" if tenant.admin_contact_email else "cleared",
        ctx.tenant_id,
    )
    return {"adminContactEmail": tenant.admin_contact_email}


class DisplayUrlRequest(BaseModel):
    """ADR-0046 follow-up: the Forge resolver pushes the user-facing
    `siteUrl` + `environmentId` from the resolver context so the
    backend can populate `tenant.display_url` and `tenant.forge_env_id`.
    The FIT carries the install identity but NOT these display fields —
    Atlassian's canonical-but-unroutable `cloud-{uuid}.atlassian.net`
    form is all the backend knows otherwise, and the deep-link URL
    pattern needs `envId` to construct the in-app destination.

    Both fields are individually optional in the body (the resolver
    sends what it has on the context; pushing one but not the other
    is acceptable while we ride out the deploy window).

    Idempotent: re-sending the same values is a no-op DB-side.
    """

    display_url: str | None = None
    env_id: str | None = None


@router.put("/display-url", status_code=200)
def set_display_url(
    body: DisplayUrlRequest,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> dict[str, str | None]:
    """Persist the user-facing site URL + Forge env_id on the tenant.
    Called by the Forge dashboard resolver on every dashboard mount
    (cheap, idempotent).

    Validates `display_url` has the `.atlassian.net` shape — defense
    against a future bug accidentally storing the canonical
    `cloud-{uuid}` form here too (which would defeat the whole point
    of the column). `env_id` is accepted verbatim — Forge environment
    IDs are opaque UUIDs with no syntactic shape the backend can
    pre-validate beyond "non-empty string."
    """
    tenant = db.execute(select(Tenant).where(Tenant.client_key == ctx.tenant_id)).scalar_one()
    dirty = False

    if body.display_url is not None:
        raw = body.display_url.strip().rstrip("/")
        # The canonical opaque form is `https://cloud-{uuid}.atlassian.net`.
        # If the resolver accidentally pushes that (it shouldn't — siteUrl
        # is the friendly form), refuse the write so we don't poison the
        # column. The middleware-side base_url already covers that case.
        if not raw or "cloud-" in raw or not raw.endswith(".atlassian.net"):
            logger.warning(
                "Refusing to persist display_url that doesn't look user-facing: %s",
                raw,
            )
        elif tenant.display_url != raw:
            tenant.display_url = raw
            dirty = True

    if body.env_id is not None:
        env_raw = body.env_id.strip()
        if env_raw and tenant.forge_env_id != env_raw:
            tenant.forge_env_id = env_raw
            dirty = True

    if dirty:
        db.commit()
        logger.info(
            "Persisted display fields for tenant=%s: display_url=%s env_id=%s",
            ctx.tenant_id,
            tenant.display_url,
            tenant.forge_env_id,
        )
    return {"displayUrl": tenant.display_url, "envId": tenant.forge_env_id}


@router.post("/ingest", status_code=200)
def ingest(
    body: IngestRequest,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> dict[str, int]:
    report = process_payloads(db, body.payloads, ctx, skip_if_stale=body.skip_if_stale)
    # Only recompute metrics for the issues that just came in. Per-tenant
    # cost is O(payload size), not O(total issues) — keeps the sync inside
    # Forge's 25s resolver budget as a tenant grows.
    touched_ids = [str(p.get("id") or p.get("key")) for p in body.payloads if p]
    touched_ids = [i for i in touched_ids if i]
    recompute_issue_metrics_for(db, ctx, touched_ids)
    # ADR-0037: live-webhook ingests tighten alert detection latency by
    # evaluating the just-changed issues' per-issue rules immediately. Gated
    # on run_alert_eval so bulk backfill/reconcile don't burst-evaluate
    # historical issues (the daily/hourly sweep handles those). Persist-only
    # in phase 2; dispatch layers on in phase 3.
    if body.run_alert_eval:
        fired = evaluate_issue_alerts(db, ctx, touched_ids)
        dispatch_alerts(db, ctx, fired)
    # Stamp the tenant's last_sync_at so subsequent calls can bound the JQL
    # to `updated >= <this>`.
    tenant = db.execute(select(Tenant).where(Tenant.client_key == ctx.tenant_id)).scalar_one()
    tenant.last_sync_at = utcnow()
    db.commit()
    logger.info(
        "Forge sync ingested for tenant=%s issues=%s transitions=%s slices=%s errors=%s",
        ctx.tenant_id,
        report.issues_processed,
        report.transitions_written,
        report.slices_written,
        len(report.errors),
    )
    return {
        "issues": report.issues_processed,
        "transitions": report.transitions_written,
        "slices": report.slices_written,
        "errors": len(report.errors),
    }


@router.delete("/issues/{issue_id}", status_code=204)
def delete_issue(
    issue_id: str,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> None:
    """Delete an issue and its descendants (transitions, slices, sprint
    membership) on `avi:jira:deleted:issue` events.

    All descendants are CASCADE-deleted via FK. We don't 404 silently
    when the issue doesn't exist — webhooks can fire for issues we never
    synced (out-of-window) and that's fine.
    """
    issue = db.get(Issue, (ctx.tenant_id, issue_id))
    if issue is None:
        # 200-equivalent: nothing to do, no error.
        return None
    db.delete(issue)
    db.commit()
    logger.info(
        "Forge webhook delete: tenant=%s issue=%s key=%s",
        ctx.tenant_id,
        issue_id,
        issue.key,
    )
    return None


class SprintIngestPayload(BaseModel):
    """One sprint as fetched by the Forge resolver from `/rest/agile/1.0/`.

    `issues` is the list of issue IDs (numeric strings, matching the
    `issues.id` primary key) currently in the sprint. Issues that aren't
    yet in our DB are silently skipped — they'll get reattached on the
    next ingest after the issue sync catches up.
    """

    id: int
    name: str
    state: str  # "active" | "closed" | "future"
    boardId: int
    projectKey: str | None = None
    startDate: str | None = None
    endDate: str | None = None
    completeDate: str | None = None
    issues: list[str] = []


class SprintsIngestRequest(BaseModel):
    sprints: list[SprintIngestPayload]


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    return dtparser.isoparse(raw)


@router.post("/sprints/ingest", status_code=200)
def ingest_sprints(
    body: SprintsIngestRequest,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> dict[str, int]:
    """Receive Jira sprint payloads + per-sprint issue membership.

    Sprints upsert idempotently. Membership is computed by union across
    the payload (an issue in sprints 41+42 ends up linked to both), then
    `set_issue_sprints` replaces each issue's full membership in one go.
    Issues we don't yet have rows for are skipped without erroring — the
    next ingest after the issue sync catches up will reattach them.
    """
    # Build the union: issue_id -> set[sprint_id]
    membership: dict[str, set[int]] = {}
    sprint_count = 0
    referenced_issue_ids: set[str] = set()
    for sprint in body.sprints:
        upsert_sprint(
            db,
            ctx.tenant_id,
            sprint_id=sprint.id,
            name=sprint.name,
            state=sprint.state,
            board_id=sprint.boardId,
            project_key=sprint.projectKey,
            start_at=_parse_dt(sprint.startDate),
            end_at=_parse_dt(sprint.endDate),
            complete_at=_parse_dt(sprint.completeDate),
        )
        sprint_count += 1
        for issue_id in sprint.issues:
            membership.setdefault(issue_id, set()).add(sprint.id)
            referenced_issue_ids.add(issue_id)

    known_issue_ids: set[str] = set()
    if referenced_issue_ids:
        known_issue_ids = set(
            db.scalars(
                select(Issue.id).where(
                    Issue.tenant_id == ctx.tenant_id,
                    Issue.id.in_(referenced_issue_ids),
                )
            )
        )
    skipped_issues = len(referenced_issue_ids - known_issue_ids)
    member_count = 0
    for issue_id, sprint_ids in membership.items():
        if issue_id not in known_issue_ids:
            continue
        set_issue_sprints(db, ctx.tenant_id, issue_id, sprint_ids)
        member_count += len(sprint_ids)

    db.commit()
    logger.info(
        "Forge sprint sync for tenant=%s sprints=%d members=%d skipped_issues=%d",
        ctx.tenant_id,
        sprint_count,
        member_count,
        skipped_issues,
    )
    return {
        "sprints": sprint_count,
        "members": member_count,
        "skipped_issues": skipped_issues,
    }
