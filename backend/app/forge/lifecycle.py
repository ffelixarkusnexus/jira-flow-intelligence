"""Forge tenant lifecycle.

Forge does not ship an explicit `installed` event we have to act on — the
Forge runtime allocates the install context and starts sending FITs as
soon as the admin completes the install flow. So we lazy-upsert the
tenant row on the first FIT-authenticated request: simpler than a webhook
flow, race-free, and idempotent across re-installs.

`uninstall` is the one Forge event we cannot derive lazily — once the
admin uninstalls, no further FITs arrive — so the Forge resolver
forwards the uninstall event to the backend, which hard-deletes the
tenant row. CASCADE on every FK (ADR-0014) cleans up the rest.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.logging import get_logger
from app.db.models import Tenant
from app.forge.fit_auth import ForgeContext

logger = get_logger(__name__)


def upsert_forge_tenant(db: Session, ctx: ForgeContext) -> Tenant:
    """Find or create the Tenant row for a Forge install. Updates `cloud_id`
    if it changed (e.g. site moved between cloud IDs — rare but possible)."""
    stmt = select(Tenant).where(Tenant.forge_installation_id == ctx.installation_id)
    tenant = db.execute(stmt).scalar_one_or_none()
    if tenant is None:
        tenant = Tenant(
            client_key=ctx.installation_id,
            cloud_id=ctx.cloud_id,
            forge_installation_id=ctx.installation_id,
            # Real base URL isn't carried in the FIT; we synthesize a
            # placeholder until a sync against the live API surfaces the
            # canonical URL. Not used for auth, only for display.
            base_url=f"https://cloud-{ctx.cloud_id}.atlassian.net",
            installed_at=utcnow(),
        )
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
        logger.info(
            "Forge tenant created: cloud_id=%s install=%s", ctx.cloud_id, ctx.installation_id
        )
        return tenant

    if tenant.cloud_id != ctx.cloud_id:
        tenant.cloud_id = ctx.cloud_id
        db.commit()
        db.refresh(tenant)
    return tenant


def delete_forge_tenant(db: Session, installation_id: str) -> bool:
    """Hard-delete a Forge tenant by installation ARI. Returns True iff a
    row existed and was removed."""
    stmt = select(Tenant).where(Tenant.forge_installation_id == installation_id)
    tenant = db.execute(stmt).scalar_one_or_none()
    if tenant is None:
        return False
    db.delete(tenant)
    db.commit()
    logger.info("Forge tenant deleted: install=%s", installation_id)
    return True
