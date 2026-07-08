from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TypeGuard

from dateutil import parser as dtparser
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.logging import get_logger
from app.core.tenant_context import TenantContext
from app.db.models import Issue
from app.services.jira_client import JiraClient
from app.services.slicing_service import recompute_slices_for_issue
from app.services.sprint_service import set_issue_sprints, upsert_sprint
from app.services.transition_service import (
    extract_transitions_from_payload,
    replace_transitions,
)

logger = get_logger(__name__)


@dataclass
class SyncReport:
    issues_processed: int = 0
    transitions_written: int = 0
    slices_written: int = 0
    errors: list[str] = field(default_factory=list)


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    return dtparser.isoparse(raw)


def _resolve_done_at(payload: dict[str, Any], done_statuses: set[str]) -> datetime | None:
    fields = payload.get("fields", {})
    resolution = _parse_dt(fields.get("resolutiondate"))
    if resolution is not None:
        return resolution
    status_name = (fields.get("status") or {}).get("name")
    if status_name in done_statuses:
        histories = (
            payload.get("changelog", {}).get("histories")
            or payload.get("changelog", {}).get("values")
            or []
        )
        latest: datetime | None = None
        for h in histories:
            ts = _parse_dt(h.get("created"))
            if ts is None:
                continue
            for item in h.get("items", []):
                if (
                    item.get("field") == "status"
                    and item.get("toString") in done_statuses
                    and (latest is None or ts > latest)
                ):
                    latest = ts
        return latest
    return None


def upsert_issue_from_payload(
    session: Session, payload: dict[str, Any], ctx: TenantContext
) -> Issue:
    fields = payload.get("fields", {})
    done_statuses = set(ctx.done_statuses)

    issue_id = payload["id"]
    key = payload["key"]
    project_key = (fields.get("project") or {}).get("key")
    summary = fields.get("summary")
    issue_type = (fields.get("issuetype") or {}).get("name")
    created_at = _parse_dt(fields.get("created")) or utcnow()
    updated_at = _parse_dt(fields.get("updated")) or created_at
    current_status = (fields.get("status") or {}).get("name")
    done_at = _resolve_done_at(payload, done_statuses)

    # WIP-Aging chart inputs.
    assignee_field = fields.get("assignee") or {}
    assignee = assignee_field.get("displayName") or None
    # accountId is the canonical Atlassian user identifier and is what
    # the Personal Data Reporting API
    # (https://developer.atlassian.com/platform/forge/user-privacy-guidelines/)
    # uses to mark accounts for anonymization. Persist alongside the
    # display name so the periodic poller can act on closed
    # accounts.
    assignee_account_id = assignee_field.get("accountId") or None
    priority = ((fields.get("priority") or {}).get("name")) or None
    story_points = _extract_story_points(fields, ctx)

    issue = session.get(Issue, (ctx.tenant_id, issue_id))
    if issue is None:
        issue = Issue(tenant_id=ctx.tenant_id, id=issue_id)
        session.add(issue)

    issue.key = key
    issue.project_key = project_key
    issue.summary = summary
    issue.issue_type = issue_type
    issue.created_at = created_at
    issue.updated_at = updated_at
    issue.done_at = done_at
    issue.current_status = current_status
    issue.assignee = assignee
    issue.assignee_account_id = assignee_account_id
    issue.priority = priority
    issue.story_points = story_points
    issue.raw_payload = payload
    return issue


# Default Jira-Software story-points custom-field ID. Per-tenant override
# lives on TenantContext (a settings UI will surface this). For now we look
# at the configured ID first, fall back to common variants.
_DEFAULT_STORY_POINTS_FIELDS = (
    "customfield_10016",
    "customfield_10026",
    "customfield_10002",
    "customfield_10004",
)


def _extract_story_points(fields: dict[str, Any], ctx: TenantContext) -> float | None:
    configured = getattr(ctx, "story_points_field_id", None)
    candidates: list[str] = [configured] if configured else []
    candidates.extend(f for f in _DEFAULT_STORY_POINTS_FIELDS if f not in candidates)
    for field_id in candidates:
        raw = fields.get(field_id)
        if raw is None:
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None


# Default Jira-Software Sprint custom-field IDs. Sprint awareness reads sprint
# membership + metadata directly from the issue payload, so no extra Jira
# API call is needed (and no Forge scope re-grant). Per-tenant override
# lands in the settings UI.
_DEFAULT_SPRINT_FIELDS = (
    "customfield_10020",  # Jira Cloud default since 2018
    "customfield_10010",  # earlier Cloud default
    "customfield_10000",  # legacy
    "customfield_10001",
)


def _looks_like_sprint_array(raw: Any) -> TypeGuard[list[dict[str, Any]]]:
    """A Sprint custom field is always a list of dicts where every entry
    has both an int `id` and a string `state` ("active"/"closed"/"future").
    The combination is distinctive enough that no other Jira custom field
    type matches — letting us probe an unknown payload without false
    positives on other array-of-object fields like Components or Versions
    (those carry `id` but not `state`)."""
    if not isinstance(raw, list) or not raw:
        return False
    for entry in raw:
        if not isinstance(entry, dict):
            return False
        if not isinstance(entry.get("id"), int):
            return False
        if not isinstance(entry.get("state"), str):
            return False
    return True


def _extract_sprints(fields: dict[str, Any], ctx: TenantContext) -> list[dict[str, Any]]:
    """Pull the Sprint custom field as a list of sprint payloads. Each
    sprint is a dict with at least `id`; other fields (`name`, `state`,
    `boardId`, `startDate`, `endDate`, `completeDate`) drive the upsert.
    Returns [] when no sprint field is populated (Kanban-only projects).

    Resolution order:
      1. `ctx.sprint_field_id` — per-tenant configured override.
      2. Static fallback list of common Cloud Sprint custom-field IDs.
      3. Heuristic probe: any `customfield_*` whose value matches the
         Sprint-array shape (list of dicts with int `id` + str `state`).
         Catches sites whose Sprint field has a non-standard ID — e.g.
         example-tenant uses customfield_10007.
    """
    configured = getattr(ctx, "sprint_field_id", None)
    candidates: list[str] = [configured] if configured else []
    candidates.extend(f for f in _DEFAULT_SPRINT_FIELDS if f not in candidates)
    for field_id in candidates:
        raw = fields.get(field_id)
        if _looks_like_sprint_array(raw):
            return [s for s in raw if isinstance(s.get("id"), int)]

    # Heuristic: walk every customfield_* in the payload looking for
    # Sprint-shaped data. Cheap — typical issue has 10-50 custom fields.
    for key, raw in fields.items():
        if not key.startswith("customfield_"):
            continue
        if _looks_like_sprint_array(raw):
            return [s for s in raw if isinstance(s.get("id"), int)]
    return []


def _ingest_sprints_from_issue(
    session: Session,
    ctx: TenantContext,
    issue_id: str,
    project_key: str | None,
    sprints: list[dict[str, Any]],
) -> None:
    """Upsert each sprint found on this issue, then replace the issue's
    sprint membership. Called per-issue from `process_issue_payload`."""
    if not sprints:
        # Issue has no sprint field — clear any prior membership too. This
        # handles tickets removed from a sprint between syncs.
        set_issue_sprints(session, ctx.tenant_id, issue_id, [])
        return
    sprint_ids: list[int] = []
    for s in sprints:
        sid = int(s["id"])
        sprint_ids.append(sid)
        upsert_sprint(
            session,
            ctx.tenant_id,
            sprint_id=sid,
            name=str(s.get("name") or f"Sprint {sid}"),
            state=str(s.get("state") or "active"),
            board_id=int(s.get("boardId") or 0),
            project_key=project_key,
            start_at=_parse_dt(s.get("startDate")),
            end_at=_parse_dt(s.get("endDate")),
            complete_at=_parse_dt(s.get("completeDate")),
            raw_payload=s,
        )
    set_issue_sprints(session, ctx.tenant_id, issue_id, sprint_ids)


def process_issue_payload(
    session: Session,
    payload: dict[str, Any],
    ctx: TenantContext,
    report: SyncReport,
    *,
    skip_if_stale: bool = False,
) -> bool:
    """Process a single issue payload. Returns True if it was processed,
    False if skipped due to staleness.

    `skip_if_stale=True` (webhook path): if our DB already has this
    issue with `updated_at >= payload.updated`, skip the work. Defends
    against out-of-order webhook deliveries and duplicate events. The
    bulk Sync Jira path leaves this False because Force-full needs to
    re-process even apparently-unchanged issues to pick up schema or
    field-list changes.
    """
    if skip_if_stale:
        existing = session.get(Issue, (ctx.tenant_id, payload["id"]))
        if existing is not None:
            new_updated = _parse_dt(payload.get("fields", {}).get("updated"))
            if new_updated is not None and existing.updated_at >= new_updated:
                return False

    issue = upsert_issue_from_payload(session, payload, ctx)
    session.flush()

    transitions = extract_transitions_from_payload(ctx.tenant_id, issue.id, payload)
    report.transitions_written += replace_transitions(session, ctx.tenant_id, issue.id, transitions)
    session.flush()

    session.refresh(issue, attribute_names=["transitions"])
    report.slices_written += recompute_slices_for_issue(session, issue, ctx)

    # Sprint metadata + membership from the issue's Sprint custom field
    # (see ADR-0023 + ingestion notes). Failures here don't
    # break the issue ingest; sprint awareness is best-effort per issue.
    fields = payload.get("fields", {})
    sprints = _extract_sprints(fields, ctx)
    try:
        _ingest_sprints_from_issue(session, ctx, issue.id, issue.project_key, sprints)
    except Exception:
        logger.exception("Failed to ingest sprints for issue %s", issue.key)

    report.issues_processed += 1
    return True


def process_payloads(
    session: Session,
    payloads: Iterable[dict[str, Any]],
    ctx: TenantContext,
    *,
    skip_if_stale: bool = False,
) -> SyncReport:
    report = SyncReport()
    for payload in payloads:
        try:
            process_issue_payload(session, payload, ctx, report, skip_if_stale=skip_if_stale)
        except Exception as exc:
            logger.exception("Failed to process issue %s", payload.get("key"))
            report.errors.append(f"{payload.get('key')}: {exc}")
    return report


async def sync_from_jira(
    session: Session,
    ctx: TenantContext,
    jql: str | None = None,
) -> SyncReport:
    report = SyncReport()
    async with JiraClient(ctx.settings) as client:
        async for issue in client.search_issues(jql=jql):
            try:
                process_issue_payload(session, issue, ctx, report)
            except Exception as exc:
                logger.exception("Failed to process issue %s", issue.get("key"))
                report.errors.append(f"{issue.get('key')}: {exc}")
    session.commit()
    return report
