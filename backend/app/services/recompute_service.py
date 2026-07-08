"""ADR-0043: recompute every historical time_slices row under the tenant's
active work schedule.

Triggered when a user enables, edits, or disables their work schedule via
`POST /api/forge/schedule/activate`. The activation endpoint persists the
new schedule state, sets `tenants.recompute_status='pending'`, and enqueues
a recompute task on the Forge consumer queue.

Each consumer invocation calls `recompute_batch(...)` which:
  * Loads up to BATCH_SIZE time_slices for the tenant (oldest first)
  * Recomputes `duration_seconds` via working_seconds_between against the
    current active schedule
  * Bulk-writes the updated rows
  * Returns the new progress percentage so the consumer can decide whether
    to re-enqueue itself for the next batch

Idempotency: working_seconds_between is deterministic for a given
(start, end, schedule) triple. Re-running an already-recomputed batch
produces the same duration_seconds values; safe to re-run on crash.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.db.models import Alert, TimeSlice, WorkSchedule
from app.services.working_time import working_seconds_between

BATCH_SIZE = 1000


def recompute_batch(
    session: Session,
    tenant_id: str,
    schedule: WorkSchedule | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Recompute one batch (BATCH_SIZE rows) of time_slices for the tenant.

    Returns a dict shaped:
        {
            "processed_in_batch": int,
            "total_rows": int,
            "progress_pct": int (0-100),
            "done": bool,
        }

    The caller (consumer) re-enqueues itself with the tenant id whenever
    `done` is False.
    """
    now = now or utcnow()

    total = session.scalar(
        select(func.count()).select_from(TimeSlice).where(TimeSlice.tenant_id == tenant_id)
    )
    if total is None:
        total = 0

    # Pull the next batch of unrecomputed rows. We don't track a per-row
    # `recompute_generation` column — instead, we order by start_at and use
    # the progress percentage to know how far we are. This makes the algorithm
    # idempotent: re-running picks up where the last invocation left off.
    #
    # Pagination strategy: take the progress percentage stored on the tenant
    # row to skip to the right offset. (Robust to a single-batch crash; for
    # a multi-batch crash sequence the recompute might re-process a batch,
    # which is correct because the same input produces the same output.)
    progress_offset = _progress_to_offset(session, tenant_id, total)

    rows = list(
        session.scalars(
            select(TimeSlice)
            .where(TimeSlice.tenant_id == tenant_id)
            .order_by(TimeSlice.issue_id.asc(), TimeSlice.start_at.asc())
            .offset(progress_offset)
            .limit(BATCH_SIZE)
        )
    )
    processed = 0
    for s in rows:
        end_ref = s.end_at if not s.is_open else now
        new_duration = working_seconds_between(s.start_at, end_ref, schedule)
        if new_duration != s.duration_seconds:
            s.duration_seconds = new_duration
        processed += 1
    session.flush()

    new_offset = progress_offset + processed
    new_pct = 100 if total == 0 else min(100, int(new_offset / total * 100))
    done = new_offset >= total

    return {
        "processed_in_batch": processed,
        "total_rows": total,
        "progress_pct": new_pct,
        "done": done,
    }


def _progress_to_offset(session: Session, tenant_id: str, total: int) -> int:
    """Read the persisted rows-processed cursor. On resume we re-process at
    most one BATCH_SIZE worth of rows (idempotent — the same input + the
    same schedule produces the same duration_seconds)."""
    offset = session.scalar(
        select(_TenantProgress.recompute_rows_processed).where(
            _TenantProgress.client_key == tenant_id
        )
    )
    return int(offset or 0)


# Helper view: read tenants.recompute_progress_pct without importing Tenant
# directly (the Tenant model is large + would create circular concerns).
# Inline shim — keeps recompute_service decoupled from anything except
# WorkSchedule + TimeSlice.
from app.db.models import Tenant as _TenantProgress  # noqa: E402


def mark_recompute_failed(session: Session, tenant_id: str, error: str) -> None:
    """Set the tenant's recompute state to failed + persist the error
    message so the dashboard banner can render the Retry button with
    context. The failure-email path is invoked by the caller (consumer)
    so the maintainer gets notified (CLAUDE.md rule #9)."""
    session.execute(
        update(_TenantProgress)
        .where(_TenantProgress.client_key == tenant_id)
        .values(recompute_status="failed", recompute_error=error)
    )
    session.flush()


def mark_recompute_running(
    session: Session,
    tenant_id: str,
    rows_processed: int,
    *,
    started_at: datetime | None = None,
) -> None:
    """Update progress without changing the run state. The consumer calls
    this after each successful batch with the cumulative rows_processed."""
    session.execute(
        update(_TenantProgress)
        .where(_TenantProgress.client_key == tenant_id)
        .values(
            recompute_status="running",
            recompute_rows_processed=rows_processed,
            recompute_started_at=started_at or utcnow(),
            recompute_error=None,
        )
    )
    session.flush()


def mark_recompute_completed(session: Session, tenant_id: str, total_rows: int) -> None:
    """Final state. Dashboard banner auto-dismisses when it sees this."""
    session.execute(
        update(_TenantProgress)
        .where(_TenantProgress.client_key == tenant_id)
        .values(
            recompute_status="completed",
            recompute_rows_processed=total_rows,
            recompute_error=None,
        )
    )
    session.flush()


def reset_alerts_for_recompute(session: Session, tenant_id: str) -> None:
    """When durations change, idempotency-keyed alerts (cycle_time /
    no_activity / status_duration) computed under the OLD math may now be
    stale — a ticket that was past threshold under calendar math may now be
    under threshold (or vice versa). Clear the alerts table so the next
    eval cycle re-emits under the new math.

    This is safe because ADR-0041's daily-bucket key shape means the next
    eval still respects "once per UTC day" — clearing today's rows lets
    today's eval re-emit under the new math."""
    session.query(Alert).filter(Alert.tenant_id == tenant_id).delete()
    session.flush()
