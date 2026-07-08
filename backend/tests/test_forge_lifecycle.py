"""Unit tests for Forge tenant lifecycle (upsert + delete) and the
uninstall router that wraps them."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jwt import PyJWK
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Tenant
from app.db.session import get_db
from app.forge.fit_auth import EXPECTED_ISSUER, ForgeContext, SigningKeyResolver
from app.forge.lifecycle import delete_forge_tenant, upsert_forge_tenant
from app.forge.middleware import ForgeAuthMiddleware
from app.routers import forge_lifecycle

INSTALL_ARI = "ari:cloud:ecosystem::installation/0a3a7799-53ae-4a5b-9e7e-03338980abb5"
APP_ARI = "ari:cloud:ecosystem::app/ef458a66-330e-4df7-a84f-d856349d84e0"
CLOUD_A = "cloud-a"
CLOUD_B = "cloud-b"


def _enable_fks(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys = ON")
    finally:
        cursor.close()


@pytest.fixture
def session_factory() -> Iterator[Callable[[], Any]]:
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    event.listen(engine, "connect", _enable_fks)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    yield factory
    engine.dispose()


def _ctx(cloud_id: str = CLOUD_A, install: str = INSTALL_ARI) -> ForgeContext:
    return ForgeContext(cloud_id=cloud_id, installation_id=install, app_id=APP_ARI)


# ----- upsert --------------------------------------------------------------


def test_upsert_creates_new_tenant(session_factory: Callable[[], Any]) -> None:
    db = session_factory()
    try:
        tenant = upsert_forge_tenant(db, _ctx())
        assert tenant.client_key == INSTALL_ARI
        assert tenant.forge_installation_id == INSTALL_ARI
        assert tenant.cloud_id == CLOUD_A
        assert tenant.product_type == "jira"
        assert tenant.enabled is True
    finally:
        db.close()


def test_upsert_is_idempotent(session_factory: Callable[[], Any]) -> None:
    db = session_factory()
    try:
        a = upsert_forge_tenant(db, _ctx())
        b = upsert_forge_tenant(db, _ctx())
        assert a.client_key == b.client_key
        rows = db.execute(select(Tenant)).all()
        assert len(rows) == 1
    finally:
        db.close()


def test_upsert_updates_cloud_id_when_changed(session_factory: Callable[[], Any]) -> None:
    db = session_factory()
    try:
        upsert_forge_tenant(db, _ctx(cloud_id=CLOUD_A))
        updated = upsert_forge_tenant(db, _ctx(cloud_id=CLOUD_B))
        assert updated.cloud_id == CLOUD_B
        rows = db.execute(select(Tenant)).all()
        assert len(rows) == 1
    finally:
        db.close()


# ----- delete --------------------------------------------------------------


def test_delete_removes_existing_tenant(session_factory: Callable[[], Any]) -> None:
    db = session_factory()
    try:
        upsert_forge_tenant(db, _ctx())
        assert delete_forge_tenant(db, INSTALL_ARI) is True
        rows = db.execute(select(Tenant)).all()
        assert rows == []
    finally:
        db.close()


def test_delete_is_idempotent_when_missing(session_factory: Callable[[], Any]) -> None:
    db = session_factory()
    try:
        assert delete_forge_tenant(db, INSTALL_ARI) is False
    finally:
        db.close()


# ----- uninstall router ----------------------------------------------------


@pytest.fixture(scope="module")
def rsa_keypair() -> tuple[bytes, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return (
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ),
        key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ),
    )


def _pem_to_jwk(pub_pem: bytes) -> dict[str, Any]:
    import base64

    pub = serialization.load_pem_public_key(pub_pem)
    numbers = pub.public_numbers()  # type: ignore[attr-defined]

    def _b64u(n: int) -> str:
        b = n.to_bytes((n.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    return {
        "kty": "RSA",
        "alg": "RS256",
        "use": "sig",
        "kid": "k",
        "n": _b64u(numbers.n),
        "e": _b64u(numbers.e),
    }


@pytest.fixture
def stub_resolver(rsa_keypair: tuple[bytes, bytes]) -> SigningKeyResolver:
    _, pub_pem = rsa_keypair

    class _Stub:
        def get_signing_key_from_jwt(self, _t: str) -> PyJWK:
            return PyJWK(_pem_to_jwk(pub_pem))

    return _Stub()


@pytest.fixture
def make_token(rsa_keypair: tuple[bytes, bytes]) -> Callable[[], str]:
    priv_pem, _ = rsa_keypair

    def _build() -> str:
        now = int(time.time())
        return jwt.encode(
            {
                "iss": EXPECTED_ISSUER,
                "aud": APP_ARI,
                "iat": now,
                "nbf": now,
                "exp": now + 60,
                "jti": "j",
                "app": {"installationId": INSTALL_ARI},
                "context": {"cloudId": CLOUD_A},
            },
            priv_pem,
            algorithm="RS256",
            headers={"kid": "k"},
        )

    return _build


@pytest.fixture
def router_client(
    session_factory: Callable[[], Session],
    stub_resolver: SigningKeyResolver,
    make_token: Callable[[], str],
) -> tuple[TestClient, str, Callable[[], Session]]:
    """A real FastAPI app mounting forge_lifecycle.router behind ForgeAuthMiddleware."""
    app = FastAPI()
    app.include_router(forge_lifecycle.router, prefix="/api")

    def _override_get_db() -> Iterator[Session]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    app.add_middleware(
        ForgeAuthMiddleware,
        forge_app_id=APP_ARI,
        session_factory=session_factory,
        resolver=stub_resolver,
    )
    return TestClient(app), make_token(), session_factory


def test_uninstall_router_deletes_existing_tenant(
    router_client: tuple[TestClient, str, Callable[[], Session]],
) -> None:
    client, token, factory = router_client
    db = factory()
    try:
        upsert_forge_tenant(
            db, ForgeContext(cloud_id=CLOUD_A, installation_id=INSTALL_ARI, app_id=APP_ARI)
        )
    finally:
        db.close()

    res = client.post(
        "/api/forge/lifecycle/uninstalled",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    assert res.json() == {"deleted": True}

    db = factory()
    try:
        assert db.execute(select(Tenant)).all() == []
    finally:
        db.close()


def test_uninstall_router_returns_false_when_no_tenant(
    router_client: tuple[TestClient, str, Callable[[], Session]],
) -> None:
    client, token, _ = router_client
    res = client.post(
        "/api/forge/lifecycle/uninstalled",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    assert res.json() == {"deleted": False}
