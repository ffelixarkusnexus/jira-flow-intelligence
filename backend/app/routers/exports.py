"""CSV exports (Feature 4 from the TMT-gap workstream / 2026-06-06).

One-shot streaming CSV of issue / time-slice data scoped to a project +
window. Powers the dashboard's "Export CSV" button. Tenant-scoped via the
standard FIT-auth dependency.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.deps import current_tenant_context
from app.core.tenant_context import TenantContext
from app.db.models import Issue, TimeSlice
from app.db.session import get_db

router = APIRouter(prefix="/export", tags=["exports"])

# ADR-0042: the external-blocking marker is included in the CSV so a customer
# pulling the data into their own spreadsheet can see which slices were
# excluded from bottleneck attribution. Time itself is still surfaced — the
# CSV is a transparent dump, not an opinion.

ViewName = Literal["bottleneck", "metrics"]
_HEADERS_BOTTLENECK = [
    "issue_key",
    "issue_summary",
    "project_key",
    "current_status",
    "issue_created_at",
    "issue_done_at",
    "slice_status",
    "slice_start_at",
    "slice_end_at",
    "slice_duration_seconds",
    "slice_is_open",
    "external_blocking",
    "is_terminal",
]


def _csv_filename(project: str, window: str) -> str:
    safe_project = project.replace("/", "_").replace("\\", "_")[:50]
    return f"flow-intelligence-{safe_project}-{window}.csv"


@router.get("/csv")
def export_csv(
    project: str = Query(..., description="Project key to scope the export to"),
    days: int = Query(default=30, ge=1, le=365, description="Window in days, from now backwards"),
    view: ViewName = Query(default="bottleneck"),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> StreamingResponse:
    """Stream a tenant-scoped CSV of issues + time slices for one project.

    One row per (issue, slice) within the window. The window applies to slice
    `start_at` — a long slice that started before the window's start but
    ended inside it is still included.
    """
    now = utcnow()
    window_start = now - timedelta(days=days)

    issues = list(
        db.scalars(
            select(Issue).where(
                Issue.tenant_id == ctx.tenant_id,
                Issue.project_key == project,
            )
        )
    )
    if not issues:
        # An empty export is a legitimate response — the customer asked for a
        # project that may have no issues yet. 200 + just the header row.
        empty_buf = io.StringIO()
        csv.writer(empty_buf).writerow(_HEADERS_BOTTLENECK)
        empty_buf.seek(0)
        return StreamingResponse(
            iter([empty_buf.getvalue()]),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{_csv_filename(project, f"{days}d")}"'
            },
        )

    issue_ids = [i.id for i in issues]
    issue_by_id = {i.id: i for i in issues}
    slices = list(
        db.scalars(
            select(TimeSlice)
            .where(
                TimeSlice.tenant_id == ctx.tenant_id,
                TimeSlice.issue_id.in_(issue_ids),
                TimeSlice.end_at >= window_start,
            )
            .order_by(TimeSlice.issue_id.asc(), TimeSlice.start_at.asc())
        )
    )
    if view != "bottleneck":
        # Only the bottleneck view is implemented in v1. Future views (metrics,
        # wip-aging) keep the same endpoint shape; surface 422 for now so a
        # mis-typed query string fails loudly rather than silently dumping
        # the wrong column set.
        raise HTTPException(
            status_code=422, detail=f"view={view!r} is not implemented; supported: 'bottleneck'"
        )

    external_blocking_set = {s.casefold() for s in ctx.external_blocking_statuses}
    terminal_set = {s.casefold() for s in ctx.terminal_statuses}

    def _rows():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(_HEADERS_BOTTLENECK)
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)
        for s in slices:
            issue = issue_by_id.get(s.issue_id)
            if issue is None:
                continue
            folded = s.status.casefold()
            writer.writerow(
                [
                    issue.key,
                    issue.summary or "",
                    issue.project_key or "",
                    issue.current_status or "",
                    _iso(issue.created_at),
                    _iso(issue.done_at),
                    s.status,
                    _iso(s.start_at),
                    _iso(s.end_at),
                    s.duration_seconds,
                    "true" if s.is_open else "false",
                    "true" if folded in external_blocking_set else "false",
                    "true" if folded in terminal_set else "false",
                ]
            )
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)

    return StreamingResponse(
        _rows(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{_csv_filename(project, f"{days}d")}"'
        },
    )


def _iso(dt: datetime | None) -> str:
    return dt.isoformat() if dt is not None else ""
