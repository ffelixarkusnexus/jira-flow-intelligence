"""Personal Data Reporting endpoints — Marketplace compliance hook.

Atlassian requires every Marketplace app that stores personal data to
periodically report the accountIds it holds and act on Atlassian's
anonymization decisions. The protocol is documented at
https://developer.atlassian.com/platform/forge/user-privacy-guidelines/.

This router exposes two tenant-scoped endpoints used by the Forge
weekly scheduled trigger (`personalDataReportingResolver` in
forge-prod/src/resolvers/personal-data.ts):

- `GET  /api/forge/personal-data/accounts?cursor=<id>&limit=<n>`
  Returns up to `limit` (default 200) distinct accountIds the tenant
  has stored, with an `updatedAt` timestamp per account (the most
  recent issue.updated_at across that account's issues). Cursor-based
  paging on accountId so the resolver can chunk through tenants with
  many users without hitting Forge's 25 sec invocation limit.

- `POST /api/forge/personal-data/erase`
  Body: `{"account_ids": ["abc", "def", ...]}`. For each accountId,
  null out `assignee` + `assignee_account_id` on every issue row in
  this tenant. Used after the resolver POSTs to Atlassian's
  `report-accounts` API and receives `closed` statuses.

Both endpoints require Forge tenant context — they go through the
standard auth middleware and use `current_tenant_context` to scope
queries.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.deps import current_tenant_context
from app.core.logging import get_logger
from app.core.tenant_context import TenantContext
from app.db.models import Issue
from app.db.session import get_db

logger = get_logger(__name__)

router = APIRouter(prefix="/forge/personal-data", tags=["forge-personal-data"])


class AccountEntry(BaseModel):
    account_id: str
    updated_at: datetime


class AccountsResponse(BaseModel):
    accounts: list[AccountEntry]
    next_cursor: str | None = Field(
        default=None,
        description=(
            "Pass back as `?cursor=<id>` to fetch the next page. "
            "`null` when the caller has reached the end."
        ),
    )


class EraseRequest(BaseModel):
    account_ids: list[str] = Field(default_factory=list, max_length=500)


class EraseResponse(BaseModel):
    erased_account_ids: list[str]
    issues_updated: int


@router.get("/accounts", response_model=AccountsResponse)
def list_accounts(
    cursor: str | None = Query(
        default=None,
        description="accountId from the previous page's `next_cursor`. Omit on first call.",
    ),
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> AccountsResponse:
    """Return distinct accountIds with their most recent issue.updated_at.

    The resolver paginates through this until `next_cursor` is null,
    then batches the accumulated accountIds (max 90 per Atlassian doc)
    to `https://api.atlassian.com/app/report-accounts/`.
    """
    # Distinct accountIds with the max(updated_at) per id, ordered by
    # accountId so the cursor is monotonic (cursor = "give me ids
    # strictly greater than this"). NULLs (unassigned issues) are
    # excluded — there's no personal data to report for them.
    from sqlalchemy import func

    base = (
        select(
            Issue.assignee_account_id,
            func.max(Issue.updated_at).label("updated_at"),
        )
        .where(
            Issue.tenant_id == ctx.tenant_id,
            Issue.assignee_account_id.is_not(None),
        )
        .group_by(Issue.assignee_account_id)
        .order_by(Issue.assignee_account_id)
        .limit(limit + 1)
    )
    if cursor is not None:
        base = base.where(Issue.assignee_account_id > cursor)

    rows: list[Any] = list(db.execute(base).all())
    has_more = len(rows) > limit
    page = rows[:limit]

    next_cursor: str | None = None
    if has_more and page:
        next_cursor = page[-1][0]

    return AccountsResponse(
        accounts=[AccountEntry(account_id=r[0], updated_at=r[1]) for r in page],
        next_cursor=next_cursor,
    )


@router.post("/erase", response_model=EraseResponse)
def erase_accounts(
    body: EraseRequest,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> EraseResponse:
    """Null out assignee + assignee_account_id for the given accounts.

    Idempotent: re-running with the same accountIds is a no-op once the
    first run has erased the data. Tenant-scoped via ctx.tenant_id, so
    one tenant cannot trigger erasure for another tenant's data even
    if accountIds overlapped.
    """
    if not body.account_ids:
        return EraseResponse(erased_account_ids=[], issues_updated=0)

    stmt = (
        update(Issue)
        .where(
            Issue.tenant_id == ctx.tenant_id,
            Issue.assignee_account_id.in_(body.account_ids),
        )
        .values(assignee=None, assignee_account_id=None)
    )
    result = db.execute(stmt)
    db.commit()
    issues_updated = result.rowcount or 0

    logger.info(
        "Erased personal data for tenant=%s accounts=%d issues=%d",
        ctx.tenant_id,
        len(body.account_ids),
        issues_updated,
    )
    return EraseResponse(
        erased_account_ids=body.account_ids,
        issues_updated=issues_updated,
    )
