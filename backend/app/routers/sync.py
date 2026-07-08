from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import current_tenant_context
from app.core.tenant_context import TenantContext
from app.db.session import get_db
from app.schemas.api import IngestPayload, SyncRequest, SyncResult
from app.services.ingestion_service import process_payloads, sync_from_jira
from app.services.jira_client import JiraAuthError

router = APIRouter(prefix="/sync", tags=["sync"])


@router.post("", response_model=SyncResult)
async def trigger_sync(
    body: SyncRequest = SyncRequest(),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> SyncResult:
    try:
        report = await sync_from_jira(db, ctx, jql=body.jql)
    except JiraAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SyncResult(**report.__dict__)


@router.post("/ingest", response_model=SyncResult)
def ingest_raw(
    body: IngestPayload,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> SyncResult:
    """Direct ingestion path for testing / batch import — accepts raw Jira payloads."""
    report = process_payloads(db, body.payloads, ctx)
    db.commit()
    return SyncResult(**report.__dict__)
