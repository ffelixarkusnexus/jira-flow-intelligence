"""Settings router.

WIP-limits CRUD + per-tenant configuration (active/done/terminal status
sets, bottleneck thresholds, custom-field IDs). The Custom UI's Settings
tab talks to these endpoints.

The tenant-settings endpoints surface BOTH the override (what's stored
on `tenants.{column}` — None means inherit defaults) AND the effective
value (what `TenantContext.{property}` resolves to right now). The UI
displays the effective value with a "(default)" hint when the override
is None, and lets the admin replace it with an explicit value or reset
back to default.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import current_tenant_context
from app.core.tenant_context import TenantContext
from app.db.models import Tenant
from app.db.session import get_db
from app.schemas.api import (
    TenantSettingsIn,
    TenantSettingsOut,
    WipLimitIn,
    WipLimitOut,
    WipLimitsResponse,
)
from app.services.wip_limits_service import (
    delete_wip_limit,
    list_wip_limits,
    upsert_wip_limit,
)

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/wip-limits", response_model=WipLimitsResponse)
def get_wip_limits(
    project_key: str | None = Query(default=None),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> WipLimitsResponse:
    """When `project_key` is provided, returns the project's rows + the
    tenant-wide rows (so the Settings UI can show the full effective set).
    Without it, returns every limit configured under the tenant."""
    rows = list_wip_limits(db, ctx.tenant_id, project_key=project_key)
    return WipLimitsResponse(
        limits=[
            WipLimitOut(
                project_key=r.project_key,
                status=r.status,
                max_in_progress=r.max_in_progress,
                breach_minutes=r.breach_minutes,
            )
            for r in rows
        ]
    )


@router.put("/wip-limits", response_model=WipLimitOut)
def put_wip_limit(
    body: WipLimitIn,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> WipLimitOut:
    """Idempotent upsert. Composite key is (tenant_id, project_key, status);
    setting `project_key=None` writes the tenant-wide default row."""
    if not body.status.strip():
        raise HTTPException(status_code=422, detail="status must be non-empty")
    row = upsert_wip_limit(
        db,
        ctx.tenant_id,
        body.project_key,
        body.status,
        max_in_progress=body.max_in_progress,
        breach_minutes=body.breach_minutes,
    )
    db.commit()
    return WipLimitOut(
        project_key=row.project_key_or_none,
        status=row.status,
        max_in_progress=row.max_in_progress,
        breach_minutes=row.breach_minutes,
    )


@router.delete("/wip-limits", status_code=204)
def remove_wip_limit(
    status: str = Query(...),
    project_key: str | None = Query(default=None),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> None:
    deleted = delete_wip_limit(db, ctx.tenant_id, project_key, status)
    if not deleted:
        raise HTTPException(status_code=404, detail="limit not found")
    db.commit()


# ----- Tenant settings ------------------------------------------------------


def _tenant_settings_out(ctx: TenantContext) -> TenantSettingsOut:
    """Pack overrides (raw column values) + effective values (resolved
    via TenantContext properties) into one response."""
    return TenantSettingsOut(
        active_statuses_override=ctx.tenant.active_statuses,
        effective_active_statuses=ctx.active_statuses,
        done_statuses_override=ctx.tenant.done_statuses,
        effective_done_statuses=ctx.done_statuses,
        terminal_statuses_override=ctx.tenant.terminal_statuses,
        effective_terminal_statuses=ctx.terminal_statuses,
        external_blocking_statuses_override=ctx.tenant.external_blocking_statuses,
        effective_external_blocking_statuses=ctx.external_blocking_statuses,
        independent_done_terminal_lists=bool(ctx.tenant.independent_done_terminal_lists),
        bottleneck_time_ratio_threshold_override=ctx.tenant.bottleneck_time_ratio_threshold,
        effective_bottleneck_time_ratio_threshold=ctx.bottleneck_time_ratio_threshold,
        bottleneck_wip_ratio_threshold_override=ctx.tenant.bottleneck_wip_ratio_threshold,
        effective_bottleneck_wip_ratio_threshold=ctx.bottleneck_wip_ratio_threshold,
        bottleneck_throughput_delta_threshold_override=ctx.tenant.bottleneck_throughput_delta_threshold,
        effective_bottleneck_throughput_delta_threshold=ctx.bottleneck_throughput_delta_threshold,
        story_points_field_id=ctx.tenant.story_points_field_id,
        sprint_field_id=ctx.tenant.sprint_field_id,
    )


@router.get("/tenant", response_model=TenantSettingsOut)
def get_tenant_settings(
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> TenantSettingsOut:
    return _tenant_settings_out(ctx)


@router.put("/tenant", response_model=TenantSettingsOut)
def put_tenant_settings(
    body: TenantSettingsIn,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> TenantSettingsOut:
    """Replace all override fields with the body's values. Setting any
    field to None drops that override; explicit values replace whatever
    was there. Acts as a "reset to defaults" when every field is None."""
    tenant = db.execute(select(Tenant).where(Tenant.client_key == ctx.tenant_id)).scalar_one()

    # Validation: status lists must be non-empty if provided. Empty list
    # would silently turn off bottleneck signal (no statuses to compare).
    for label, val in (
        ("active_statuses", body.active_statuses),
        ("done_statuses", body.done_statuses),
        ("terminal_statuses", body.terminal_statuses),
    ):
        if val is not None and len(val) == 0:
            raise HTTPException(
                status_code=422,
                detail=f"{label} can be null (default) or a non-empty list, not []",
            )
    # Threshold sanity bounds — keeps an admin from accidentally setting
    # a 99x threshold and never seeing a bottleneck again.
    for label, val in (
        ("bottleneck_time_ratio_threshold", body.bottleneck_time_ratio_threshold),
        ("bottleneck_wip_ratio_threshold", body.bottleneck_wip_ratio_threshold),
    ):
        if val is not None and (val < 1.0 or val > 10.0):
            raise HTTPException(
                status_code=422,
                detail=f"{label} must be between 1.0 and 10.0",
            )
    if body.bottleneck_throughput_delta_threshold is not None and (
        body.bottleneck_throughput_delta_threshold < -1.0
        or body.bottleneck_throughput_delta_threshold > 1.0
    ):
        raise HTTPException(
            status_code=422,
            detail="bottleneck_throughput_delta_threshold must be between -1.0 and 1.0",
        )

    tenant.active_statuses = body.active_statuses
    tenant.done_statuses = body.done_statuses
    tenant.terminal_statuses = body.terminal_statuses
    # ADR-0042: external_blocking_statuses allows empty list as a valid
    # override (distinct from active/done/terminal where empty is footgun);
    # this means "I explicitly have no external-blocking statuses." None still
    # drops the override entirely.
    tenant.external_blocking_statuses = body.external_blocking_statuses
    tenant.independent_done_terminal_lists = bool(body.independent_done_terminal_lists)
    tenant.bottleneck_time_ratio_threshold = body.bottleneck_time_ratio_threshold
    tenant.bottleneck_wip_ratio_threshold = body.bottleneck_wip_ratio_threshold
    tenant.bottleneck_throughput_delta_threshold = body.bottleneck_throughput_delta_threshold
    tenant.story_points_field_id = body.story_points_field_id or None
    tenant.sprint_field_id = body.sprint_field_id or None
    db.commit()

    # Re-resolve effective values against the updated row.
    refreshed = TenantContext(tenant=tenant, settings=ctx.settings)
    return _tenant_settings_out(refreshed)
