"""Sprints router. Lists sprints for the picker."""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import current_tenant_context
from app.core.tenant_context import TenantContext
from app.db.session import get_db
from app.services.sprint_service import list_sprints

router = APIRouter(prefix="/sprints", tags=["sprints"])


class SprintOut(BaseModel):
    id: int
    name: str
    state: str
    start_at: datetime | None
    end_at: datetime | None
    complete_at: datetime | None
    board_id: int
    project_key: str | None


class SprintsResponse(BaseModel):
    sprints: list[SprintOut]


@router.get("", response_model=SprintsResponse)
def get_sprints(
    project_key: str | None = Query(default=None),
    state: str | None = Query(default=None, regex="^(active|closed|future)$"),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> SprintsResponse:
    """Project-scoped sprint list, ordered most-recent first. The picker
    uses this to populate "current sprint" / "previous sprint" / "last 3
    sprints" options. Empty list = picker hides the sprint section."""
    rows = list_sprints(db, ctx.tenant_id, project_key=project_key, state=state)
    return SprintsResponse(
        sprints=[
            SprintOut(
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
    )
