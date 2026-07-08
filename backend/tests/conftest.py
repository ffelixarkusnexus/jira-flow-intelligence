from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.tenant_context import TenantContext
from app.db.models import Base, Tenant


def _enable_fks(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys = ON")
    finally:
        cursor.close()


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    event.listen(eng, "connect", _enable_fks)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    SessionFactory = sessionmaker(bind=engine, autoflush=False, future=True)
    s = SessionFactory()
    try:
        yield s
    finally:
        s.close()


def make_tenant(session: Session, client_key: str = "test-tenant") -> Tenant:
    tenant = Tenant(
        client_key=client_key,
        cloud_id=f"{client_key}-cloud",
        base_url=f"https://{client_key}.atlassian.net",
        display_url=f"https://{client_key}.atlassian.net",
        product_type="jira",
        forge_installation_id=client_key,
        plan="free",
        enabled=True,
        installed_at=utcnow(),
    )
    session.add(tenant)
    session.flush()
    return tenant


@pytest.fixture
def tenant(session: Session) -> Tenant:
    return make_tenant(session)


@pytest.fixture
def ctx(tenant: Tenant) -> TenantContext:
    return TenantContext(tenant=tenant, settings=Settings())
