"""Sprint awareness — Jira Software sprint integration.

Two pieces:

1. Persistence helpers — `upsert_sprint`, `set_issue_sprints`, list/get
   queries used by the routers.
2. Window math — `sprint_windows` returns `((cur_s, cur_e), (prev_s, prev_e))`
   for sprint-bucketed analytics (current sprint, previous sprint, last 3
   sprints). See ADR-0023 for cross-sprint attribution semantics.

The Forge resolver POSTs sprint payloads to `/api/forge/sprints/ingest`,
which calls into `upsert_sprint` + `set_issue_sprints`. The metrics
routers accept a `sprint_id` query parameter and resolve windows via
`sprint_windows` instead of `default_windows`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import IssueSprint, Sprint


@dataclass(frozen=True)
class SprintDTO:
    id: int
    name: str
    state: str
    start_at: datetime | None
    end_at: datetime | None
    complete_at: datetime | None
    board_id: int
    project_key: str | None


def upsert_sprint(
    session: Session,
    tenant_id: str,
    *,
    sprint_id: int,
    name: str,
    state: str,
    board_id: int,
    project_key: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    complete_at: datetime | None = None,
    raw_payload: dict[str, Any] | None = None,
) -> Sprint:
    existing = session.get(Sprint, (tenant_id, sprint_id))
    if existing is None:
        existing = Sprint(
            tenant_id=tenant_id,
            id=sprint_id,
            name=name,
            state=state,
            board_id=board_id,
            project_key=project_key,
            start_at=start_at,
            end_at=end_at,
            complete_at=complete_at,
            raw_payload=raw_payload,
        )
        session.add(existing)
        session.flush()
    else:
        existing.name = name
        existing.state = state
        existing.board_id = board_id
        existing.project_key = project_key
        existing.start_at = start_at
        existing.end_at = end_at
        existing.complete_at = complete_at
        existing.raw_payload = raw_payload
    return existing


def set_issue_sprints(
    session: Session, tenant_id: str, issue_id: str, sprint_ids: Iterable[int]
) -> None:
    """Replace this issue's sprint membership with `sprint_ids`. Drops any
    rows for sprints not in the new set; inserts new ones idempotently."""
    target = set(sprint_ids)
    existing = set(
        session.scalars(
            select(IssueSprint.sprint_id).where(
                IssueSprint.tenant_id == tenant_id, IssueSprint.issue_id == issue_id
            )
        )
    )
    to_add = target - existing
    to_remove = existing - target
    if to_remove:
        session.execute(
            delete(IssueSprint).where(
                IssueSprint.tenant_id == tenant_id,
                IssueSprint.issue_id == issue_id,
                IssueSprint.sprint_id.in_(to_remove),
            )
        )
    for sid in to_add:
        session.add(IssueSprint(tenant_id=tenant_id, issue_id=issue_id, sprint_id=sid))


def list_sprints(
    session: Session,
    tenant_id: str,
    *,
    project_key: str | None = None,
    state: str | None = None,
) -> list[SprintDTO]:
    stmt = select(Sprint).where(Sprint.tenant_id == tenant_id)
    if project_key:
        stmt = stmt.where(Sprint.project_key == project_key)
    if state:
        stmt = stmt.where(Sprint.state == state)
    # Sort by start_at desc so callers get the most-recent first; closed
    # sprints with `complete_at` use that as the secondary sort key.
    stmt = stmt.order_by(Sprint.start_at.desc().nulls_last(), Sprint.id.desc())
    rows = session.scalars(stmt).all()
    return [
        SprintDTO(
            id=r.id,
            name=r.name,
            state=r.state,
            start_at=r.start_at,
            end_at=r.end_at,
            complete_at=r.complete_at,
            board_id=r.board_id,
            project_key=r.project_key,
        )
        for r in rows
    ]


def get_active_sprint(
    session: Session, tenant_id: str, project_key: str | None
) -> SprintDTO | None:
    actives = list_sprints(session, tenant_id, project_key=project_key, state="active")
    return actives[0] if actives else None


def sprint_windows(
    session: Session,
    tenant_id: str,
    *,
    project_key: str | None,
    sprint_id: int | None = None,
    span: int = 1,
    now: datetime | None = None,
) -> tuple[tuple[datetime, datetime], tuple[datetime, datetime]] | None:
    """Returns `((cur_s, cur_e), (prev_s, prev_e))` for sprint-bucketed
    analytics, or None if the project has no usable sprints.

    - `sprint_id` set → window = that sprint's bounds. Previous = the most
      recently closed sprint *before* it.
    - `sprint_id` None + active sprint exists → current = active sprint
      from start to now; previous = most recently closed sprint.
    - No active sprint, but closed sprints exist → current = most recent
      closed sprint; previous = the one before that.
    - No sprints at all → None (caller falls back to day-bucketed windows).

    `span > 1` widens the window to include the trailing N closed sprints
    (so "last 3 sprints" pulls one window covering three sprints; previous
    window covers the three before that). Used for sprint-trend framing.
    """
    if now is None:
        now = datetime.now(UTC)

    if sprint_id is not None:
        target = session.get(Sprint, (tenant_id, sprint_id))
        if target is None or target.start_at is None:
            return None
        cur_start = target.start_at
        cur_end = target.complete_at or target.end_at or now
        prev = _previous_closed_sprint_before(session, tenant_id, project_key, cur_start)
        prev_window = _bounds_or(prev, cur_start)
        return (cur_start, cur_end), prev_window

    active = get_active_sprint(session, tenant_id, project_key)
    closed = [
        s
        for s in list_sprints(session, tenant_id, project_key=project_key, state="closed")
        if s.start_at is not None
    ]

    if active is not None and active.start_at is not None:
        cur_start = active.start_at
        cur_end = now
        prev_window = _bounds_or(closed[0] if closed else None, cur_start)
        if span > 1 and len(closed) >= span:
            # Widen current to active + last (span-1) closed; previous is the
            # span before that.
            tail = closed[: span - 1]
            cur_start = tail[-1].start_at or cur_start
            prev_tail = closed[span - 1 : 2 * span - 1]
            if prev_tail:
                prev_window = (
                    prev_tail[-1].start_at or prev_window[0],
                    prev_tail[0].complete_at or prev_tail[0].end_at or cur_start,
                )
        return (cur_start, cur_end), prev_window

    if closed:
        target_idx = 0
        if span > 1 and len(closed) >= span:
            target_idx = 0
            cur = closed[:span]
            cur_start = cur[-1].start_at or now
            cur_end = cur[0].complete_at or cur[0].end_at or now
            prev_tail = closed[span : 2 * span]
            prev_window = _bounds_or(prev_tail[0] if prev_tail else None, cur_start)
            return (cur_start, cur_end), prev_window
        cur_sprint = closed[target_idx]
        cur_start = cur_sprint.start_at or now
        cur_end = cur_sprint.complete_at or cur_sprint.end_at or now
        prev_window = _bounds_or(
            closed[target_idx + 1] if len(closed) > target_idx + 1 else None, cur_start
        )
        return (cur_start, cur_end), prev_window

    return None


def _previous_closed_sprint_before(
    session: Session, tenant_id: str, project_key: str | None, before: datetime
) -> SprintDTO | None:
    candidates = [
        s
        for s in list_sprints(session, tenant_id, project_key=project_key, state="closed")
        if s.start_at is not None and s.start_at < before
    ]
    return candidates[0] if candidates else None


def _bounds_or(sprint: SprintDTO | None, fallback_end: datetime) -> tuple[datetime, datetime]:
    """Sprint bounds when the sprint exists; degenerate (fallback_end,
    fallback_end) when None — produces an empty previous window so the
    bottleneck pipeline correctly reports no previous-window signal."""
    if sprint is None or sprint.start_at is None:
        return (fallback_end, fallback_end)
    return (sprint.start_at, sprint.complete_at or sprint.end_at or fallback_end)
