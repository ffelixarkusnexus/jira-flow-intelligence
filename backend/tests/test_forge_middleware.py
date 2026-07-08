"""ForgeAuthMiddleware end-to-end via TestClient with a stub protected route.

Verifies the contract from ADR-0019:
- Skip-listed paths bypass auth (healthz, OpenAPI)
- All other paths require a verifiable Forge Invocation Token
- ForgeContext is bound to request.state.forge_ctx on success
- Tenant ORM row is lazy-upserted and bound to request.state.tenant
- The /api/forge/lifecycle/uninstalled path validates FIT but skips upsert
- Each rejection condition returns 401 with a clear `detail`
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from jwt import PyJWK
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Tenant
from app.forge.fit_auth import EXPECTED_ISSUER, SigningKeyResolver
from app.forge.middleware import ForgeAuthMiddleware

APP_ARI = "ari:cloud:ecosystem::app/ef458a66-330e-4df7-a84f-d856349d84e0"
INSTALL_ARI = "ari:cloud:ecosystem::installation/0a3a7799-53ae-4a5b-9e7e-03338980abb5"
CLOUD_ID = "11111111-2222-3333-4444-555555555555"


_DELETE = object()


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
        "kid": "test-key",
        "n": _b64u(numbers.n),
        "e": _b64u(numbers.e),
    }


@pytest.fixture
def stub_resolver(rsa_keypair: tuple[bytes, bytes]) -> SigningKeyResolver:
    _, pub_pem = rsa_keypair

    class _Stub:
        def get_signing_key_from_jwt(self, _token: str) -> PyJWK:
            return PyJWK(_pem_to_jwk(pub_pem))

    return _Stub()


@pytest.fixture
def make_token(rsa_keypair: tuple[bytes, bytes]) -> Callable[..., str]:
    priv_pem, _ = rsa_keypair

    def _build(**overrides: Any) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": EXPECTED_ISSUER,
            "aud": APP_ARI,
            "iat": now,
            "nbf": now,
            "exp": now + 60,
            "jti": "test-jti",
            "app": {"installationId": INSTALL_ARI},
            "context": {"cloudId": CLOUD_ID},
        }
        for key, value in overrides.items():
            if value is _DELETE:
                claims.pop(key, None)
            else:
                claims[key] = value
        return jwt.encode(claims, priv_pem, algorithm="RS256", headers={"kid": "test-key"})

    return _build


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


@pytest.fixture
def client(
    stub_resolver: SigningKeyResolver,
    session_factory: Callable[[], Any],
) -> TestClient:
    """Minimal app with skip-listed, protected, and uninstall-path routes."""
    app = FastAPI()

    @app.get("/healthz")
    def _healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/openapi.json")
    def _openapi() -> dict[str, str]:
        return {"openapi": "3.0.0"}

    @app.get("/api/protected")
    def _protected(request: Request) -> dict[str, str]:
        ctx = request.state.forge_ctx
        tenant: Tenant = request.state.tenant
        return {
            "cloudId": ctx.cloud_id,
            "installationId": ctx.installation_id,
            "tenantClientKey": tenant.client_key,
        }

    @app.post("/api/forge/lifecycle/uninstalled")
    def _uninstall(request: Request) -> dict[str, bool]:
        # Confirm middleware skipped the upsert: forge_ctx is bound but
        # tenant is not, so the route can decide whether to delete.
        has_forge_ctx = getattr(request.state, "forge_ctx", None) is not None
        has_tenant = getattr(request.state, "tenant", None) is not None
        return {"hasForgeCtx": has_forge_ctx, "hasTenant": has_tenant}

    app.add_middleware(
        ForgeAuthMiddleware,
        forge_app_id=APP_ARI,
        session_factory=session_factory,
        resolver=stub_resolver,
    )
    return TestClient(app)


# ----- skip list -----------------------------------------------------------


def test_healthz_bypasses_auth(client: TestClient) -> None:
    assert client.get("/healthz").status_code == 200


def test_openapi_bypasses_auth(client: TestClient) -> None:
    assert client.get("/openapi.json").status_code == 200


# ----- happy path ----------------------------------------------------------


def test_protected_route_accepts_valid_fit(
    client: TestClient, make_token: Callable[..., str]
) -> None:
    token = make_token()
    res = client.get("/api/protected", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    body = res.json()
    assert body["cloudId"] == CLOUD_ID
    assert body["installationId"] == INSTALL_ARI
    # Lazy upsert: client_key derives from the installation ARI.
    assert body["tenantClientKey"] == INSTALL_ARI


def test_protected_route_idempotent_upsert(
    client: TestClient,
    make_token: Callable[..., str],
    session_factory: Callable[[], Any],
) -> None:
    """Two calls with valid FITs against the same install must end up with
    exactly one tenant row (idempotency invariant from ADR-0011)."""
    token = make_token()
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/protected", headers=headers).status_code == 200
    assert client.get("/api/protected", headers=headers).status_code == 200
    db = session_factory()
    try:
        rows = db.execute(select(Tenant).where(Tenant.forge_installation_id == INSTALL_ARI)).all()
        assert len(rows) == 1
    finally:
        db.close()


def test_uninstall_path_validates_fit_but_skips_upsert(
    client: TestClient,
    make_token: Callable[..., str],
    session_factory: Callable[[], Any],
) -> None:
    """The middleware must NOT recreate a tenant we're about to delete."""
    token = make_token()
    res = client.post(
        "/api/forge/lifecycle/uninstalled",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    assert res.json() == {"hasForgeCtx": True, "hasTenant": False}
    db = session_factory()
    try:
        rows = db.execute(select(Tenant)).all()
        # Middleware did not upsert.
        assert rows == []
    finally:
        db.close()


# ----- 401 modes -----------------------------------------------------------


def test_protected_route_rejects_missing_token(client: TestClient) -> None:
    res = client.get("/api/protected")
    assert res.status_code == 401
    assert res.json()["detail"] == "Missing FIT"


def test_protected_route_rejects_expired_fit(
    client: TestClient, make_token: Callable[..., str]
) -> None:
    now = int(time.time())
    token = make_token(iat=now - 600, nbf=now - 600, exp=now - 60)
    res = client.get("/api/protected", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401
    assert "expired" in res.json()["detail"].lower()


def test_protected_route_rejects_wrong_audience(
    client: TestClient, make_token: Callable[..., str]
) -> None:
    token = make_token(aud="ari:cloud:ecosystem::app/some-other-app")
    res = client.get("/api/protected", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401
    assert "audience" in res.json()["detail"].lower()


def test_protected_route_rejects_garbage_token(client: TestClient) -> None:
    res = client.get("/api/protected", headers={"Authorization": "Bearer not.a.jwt"})
    assert res.status_code == 401
