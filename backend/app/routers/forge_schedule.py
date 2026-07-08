"""ADR-0043 work-schedule activation + recompute orchestration.

Endpoints:
  - POST /api/forge/schedule/activate       Persists schedule state,
                                            sets recompute_status='pending'.
                                            Frontend then invokes the Forge
                                            resolver to enqueue a recompute.
  - POST /api/forge/schedule/recompute-batch
                                            Consumer-driven: processes one
                                            batch of time_slices under the
                                            active schedule, updates progress,
                                            returns whether more remain.

The endpoint split keeps the Forge consumer's job tiny (call recompute-batch;
re-enqueue if !done) and keeps the backend deterministic + idempotent.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.deps import current_tenant_context
from app.core.tenant_context import TenantContext
from app.db.models import Tenant, WorkSchedule
from app.db.session import get_db
from app.services import recompute_service

router = APIRouter(prefix="/forge/schedule", tags=["forge-schedule"])


class WorkScheduleIn(BaseModel):
    """Activation/update payload."""

    name: str = Field(default="Default schedule")
    timezone: str = Field(default="UTC")
    working_days_mask: int = Field(
        default=31, ge=0, le=127, description="Bitfield Mon=1..Sun=64; Mon-Fri=31"
    )
    work_start_time: str = Field(default="09:00:00", description="HH:MM[:SS]")
    work_end_time: str = Field(default="17:00:00", description="HH:MM[:SS]")
    holidays: list[str] = Field(
        default_factory=list,
        description="ISO date strings (YYYY-MM-DD) in the schedule's timezone",
    )
    enabled: bool = Field(default=True)


class WorkScheduleOut(BaseModel):
    id: int | None
    name: str
    timezone: str
    working_days_mask: int
    work_start_time: str
    work_end_time: str
    holidays: list[str]
    enabled: bool


class RecomputeStatusOut(BaseModel):
    status: str  # idle | pending | running | completed | failed
    progress_pct: int  # 0-100, derived from rows_processed / total
    rows_processed: int
    total_rows: int
    started_at: datetime | None
    error: str | None


class RecomputeBatchOut(BaseModel):
    processed_in_batch: int
    total_rows: int
    progress_pct: int
    done: bool


def _schedule_to_out(s: WorkSchedule | None) -> WorkScheduleOut | None:
    if s is None:
        return None
    return WorkScheduleOut(
        id=s.id,
        name=s.name,
        timezone=s.timezone,
        working_days_mask=s.working_days_mask,
        work_start_time=s.work_start_time,
        work_end_time=s.work_end_time,
        holidays=list(s.holidays or []),
        enabled=s.enabled,
    )


@router.get("", response_model=WorkScheduleOut | None)
def get_active_schedule(
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> WorkScheduleOut | None:
    return _schedule_to_out(ctx.work_schedule)


@router.get("/status", response_model=RecomputeStatusOut)
def get_recompute_status(
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> RecomputeStatusOut:
    from sqlalchemy import func

    from app.db.models import TimeSlice

    total = (
        db.scalar(
            select(func.count()).select_from(TimeSlice).where(TimeSlice.tenant_id == ctx.tenant_id)
        )
        or 0
    )
    rows_processed = ctx.tenant.recompute_rows_processed or 0
    pct = 100 if total == 0 else min(100, int(100 * rows_processed / total))
    return RecomputeStatusOut(
        status=ctx.tenant.recompute_status or "idle",
        progress_pct=pct,
        rows_processed=rows_processed,
        total_rows=total,
        started_at=ctx.tenant.recompute_started_at,
        error=ctx.tenant.recompute_error,
    )


@router.post("/activate", response_model=WorkScheduleOut)
def activate_schedule(
    body: WorkScheduleIn,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> WorkScheduleOut:
    """Persist or update the tenant's active schedule and mark recompute as
    pending. The Forge frontend invokes a `startRecompute` resolver after
    this returns; that resolver pushes a recompute task onto the consumer
    queue (mirrors the ADR-0033 backfill pattern)."""
    # Re-fetch the tenant in the request session (ctx.tenant may have come
    # from a different session in test contexts; same pattern as
    # /api/settings/tenant in `settings.py`).
    tenant = db.execute(select(Tenant).where(Tenant.client_key == ctx.tenant_id)).scalar_one()

    # Upsert the schedule.
    schedule = None
    if tenant.active_work_schedule_id is not None:
        schedule = db.get(WorkSchedule, tenant.active_work_schedule_id)
    if schedule is None:
        schedule = WorkSchedule(
            tenant_id=ctx.tenant_id,
            name=body.name,
            timezone=body.timezone,
            working_days_mask=body.working_days_mask,
            work_start_time=body.work_start_time,
            work_end_time=body.work_end_time,
            holidays=body.holidays,
            enabled=body.enabled,
        )
        db.add(schedule)
        db.flush()
        tenant.active_work_schedule_id = schedule.id
    else:
        schedule.name = body.name
        schedule.timezone = body.timezone
        schedule.working_days_mask = body.working_days_mask
        schedule.work_start_time = body.work_start_time
        schedule.work_end_time = body.work_end_time
        schedule.holidays = body.holidays
        schedule.enabled = body.enabled

    tenant.recompute_status = "pending"
    tenant.recompute_rows_processed = 0
    tenant.recompute_started_at = utcnow()
    tenant.recompute_error = None
    db.commit()
    return _schedule_to_out(schedule)  # type: ignore[return-value]


@router.post("/recompute-batch", response_model=RecomputeBatchOut)
def recompute_one_batch(
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> RecomputeBatchOut:
    """Process one BATCH_SIZE-worth of time_slices for the tenant. Returns
    `done=True` when the last batch lands, at which point the consumer
    stops re-enqueueing itself. Idempotent — re-runs against the same
    state produce the same durations (working_seconds_between is pure).
    """
    # Re-fetch tenant + schedule in this session.
    tenant = db.execute(select(Tenant).where(Tenant.client_key == ctx.tenant_id)).scalar_one()
    schedule = (
        db.get(WorkSchedule, tenant.active_work_schedule_id)
        if tenant.active_work_schedule_id is not None
        else None
    )
    try:
        result = recompute_service.recompute_batch(db, ctx.tenant_id, schedule)
    except Exception as exc:  # surface failures via the failure-email path
        recompute_service.mark_recompute_failed(db, ctx.tenant_id, str(exc))
        db.commit()
        raise HTTPException(status_code=500, detail=f"recompute failed: {exc}") from exc

    new_total_processed = (tenant.recompute_rows_processed or 0) + result["processed_in_batch"]
    if result["done"]:
        recompute_service.mark_recompute_completed(db, ctx.tenant_id, result["total_rows"])
        # Alerts may be stale under the new math — clear so the next eval
        # re-emits cleanly under ADR-0041's daily-bucket key.
        recompute_service.reset_alerts_for_recompute(db, ctx.tenant_id)
    else:
        recompute_service.mark_recompute_running(db, ctx.tenant_id, new_total_processed)
    db.commit()
    return RecomputeBatchOut(
        processed_in_batch=result["processed_in_batch"],
        total_rows=result["total_rows"],
        progress_pct=result["progress_pct"],
        done=result["done"],
    )
