"""WIP limits — per-status configuration that turns the bare WIP averages
into actionable `current / limit` signals.

See ADR-0022 for the data model and resolution semantics. The two
operations callers need:

- `get_wip_limit(session, tenant_id, project_key, status)` — returns the
  effective limit for a (project, status) pair, falling back from project-
  scoped row to tenant-wide row to None.
- `list_wip_limits(session, tenant_id, project_key=None)` — for the
  Settings tab to display + edit. Returns project rows when project_key
  is provided, else all rows for the tenant.

The case-folded status matching mirrors `metrics_service.discover_status_groups`:
the dashboard groups "Code Review" and "CODE REVIEW" into one row, so a
limit set on "Code Review" applies to slices stored as "CODE REVIEW" too.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.db.models import WipLimit


class ResolvedLimit(NamedTuple):
    """The effective limit for a (project, status) pair plus where it came from.
    `scope` is "project" when an explicit project row matched, "tenant" when
    a NULL-project (sentinel "") row matched, "none" when no limit exists."""

    max_in_progress: int | None
    breach_minutes: int
    scope: str  # "project" | "tenant" | "none"


@dataclass(frozen=True)
class WipLimitDTO:
    project_key: str | None  # None = tenant-wide
    status: str
    max_in_progress: int
    breach_minutes: int


def get_wip_limit(
    session: Session, tenant_id: str, project_key: str | None, status: str
) -> ResolvedLimit:
    """Resolve effective limit. Project row wins; tenant-wide row falls
    back; None when neither exists. Case-folded match on status."""
    folded = status.casefold()
    candidates = session.scalars(select(WipLimit).where(WipLimit.tenant_id == tenant_id)).all()
    project_match: WipLimit | None = None
    tenant_match: WipLimit | None = None
    for row in candidates:
        if row.status.casefold() != folded:
            continue
        if project_key and row.project_key == project_key:
            project_match = row
            break
        if row.project_key == "":  # tenant-wide sentinel
            tenant_match = row
    chosen = project_match or tenant_match
    if chosen is None:
        return ResolvedLimit(max_in_progress=None, breach_minutes=0, scope="none")
    return ResolvedLimit(
        max_in_progress=chosen.max_in_progress,
        breach_minutes=chosen.breach_minutes,
        scope="project" if project_match is not None else "tenant",
    )


def list_wip_limits(
    session: Session, tenant_id: str, project_key: str | None = None
) -> list[WipLimitDTO]:
    """List configured limits. With `project_key` set, returns the union of
    that project's rows + tenant-wide rows (so the Settings UI can show the
    full effective set, marking each as project / tenant scope)."""
    rows = session.scalars(
        select(WipLimit).where(WipLimit.tenant_id == tenant_id).order_by(WipLimit.status)
    ).all()
    if project_key is None:
        return [
            WipLimitDTO(
                project_key=r.project_key_or_none,
                status=r.status,
                max_in_progress=r.max_in_progress,
                breach_minutes=r.breach_minutes,
            )
            for r in rows
        ]
    return [
        WipLimitDTO(
            project_key=r.project_key_or_none,
            status=r.status,
            max_in_progress=r.max_in_progress,
            breach_minutes=r.breach_minutes,
        )
        for r in rows
        if r.project_key in ("", project_key)
    ]


def upsert_wip_limit(
    session: Session,
    tenant_id: str,
    project_key: str | None,
    status: str,
    *,
    max_in_progress: int,
    breach_minutes: int = 0,
) -> WipLimit:
    """Insert or update. NULL project_key stored as "" — see WipLimit doc."""
    if max_in_progress < 0:
        raise ValueError("max_in_progress must be >= 0")
    if breach_minutes < 0:
        raise ValueError("breach_minutes must be >= 0")
    now = utcnow()
    pk = project_key or ""
    existing = session.get(WipLimit, (tenant_id, pk, status))
    if existing is None:
        existing = WipLimit(
            tenant_id=tenant_id,
            project_key=pk,
            status=status,
            max_in_progress=max_in_progress,
            breach_minutes=breach_minutes,
            created_at=now,
            updated_at=now,
        )
        session.add(existing)
        # Flush so a subsequent upsert in the same transaction sees this row
        # via session.get rather than colliding on the unique constraint at
        # commit time. Tests run autoflush=False; production flushes per
        # request boundary anyway.
        session.flush()
    else:
        existing.max_in_progress = max_in_progress
        existing.breach_minutes = breach_minutes
        existing.updated_at = now
    return existing


def delete_wip_limit(
    session: Session, tenant_id: str, project_key: str | None, status: str
) -> bool:
    """Returns True if a row was deleted."""
    pk = project_key or ""
    existing = session.get(WipLimit, (tenant_id, pk, status))
    if existing is None:
        return False
    session.delete(existing)
    return True
