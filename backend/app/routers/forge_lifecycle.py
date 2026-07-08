"""Forge lifecycle router.

`installed` is handled implicitly by the auth middleware (lazy upsert on
first FIT-authenticated request — see `app.forge.lifecycle`). This router
only needs `uninstalled`, which the Forge resolver forwards via
`api.fetch` on the `avi:forge:uninstalled:app` event.

The middleware validates the FIT but skips the upsert for this path
(see `app.forge.middleware.NO_UPSERT_PATHS`).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.forge.fit_auth import ForgeContext
from app.forge.lifecycle import delete_forge_tenant

router = APIRouter(prefix="/forge/lifecycle", tags=["forge-lifecycle"])


@router.post("/uninstalled", status_code=200)
def handle_uninstalled(request: Request, db: Session = Depends(get_db)) -> dict[str, bool]:
    ctx: ForgeContext | None = getattr(request.state, "forge_ctx", None)
    if ctx is None:
        # Reachable only if middleware misconfigured to skip auth here.
        raise HTTPException(status_code=401, detail="No Forge context on request")
    deleted = delete_forge_tenant(db, ctx.installation_id)
    return {"deleted": deleted}
