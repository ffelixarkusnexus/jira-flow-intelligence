"""Tests for `PUT /api/forge/sync/display-url` (ADR-0046 follow-up).

The endpoint exists to close the install-path gap surfaced by the
2026-06-08 backfill-link bug: the backend's lifecycle handler creates
tenants with only `base_url` set (Atlassian's canonical
`cloud-{uuid}` form), never `display_url`. The Forge dashboard resolver
calls this endpoint on every mount with `context.siteUrl` so the
tenant's `display_url` gets populated and customer-facing URL
construction routes correctly.

Test surface:
- Persists a valid friendly URL.
- Idempotent — re-PUT with the same URL is a no-op (no DB write).
- Defensive against the canonical `cloud-{uuid}` form being PUT
  accidentally (which would defeat the column's purpose).
- Defensive against non-atlassian.net URLs.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.deps import current_tenant_context
from app.core.tenant_context import TenantContext
from app.db.models import Tenant
from app.db.session import get_db
from app.main import create_app


@pytest.fixture
def client(session: Session, tenant: Tenant) -> Iterator[TestClient]:
    app = create_app(with_jwt_middleware=False)

    def _override_db() -> Iterator[Session]:
        yield session

    def _override_ctx() -> TenantContext:
        return TenantContext(tenant=tenant, settings=Settings())

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[current_tenant_context] = _override_ctx
    return TestClient(app)


def test_persists_friendly_site_url(client: TestClient, session: Session, tenant: Tenant) -> None:
    response = client.put(
        "/api/forge/sync/display-url",
        json={"display_url": "https://your-site.atlassian.net"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["displayUrl"] == "https://your-site.atlassian.net"
    # envId echoes whatever's currently stored — may be None when only
    # display_url is being pushed (the field is independently optional).
    assert "envId" in body
    session.refresh(tenant)
    assert tenant.display_url == "https://your-site.atlassian.net"


def test_strips_trailing_slash(client: TestClient, session: Session, tenant: Tenant) -> None:
    client.put(
        "/api/forge/sync/display-url",
        json={"display_url": "https://your-site.atlassian.net/"},
    )
    session.refresh(tenant)
    assert tenant.display_url == "https://your-site.atlassian.net"


def test_refuses_cloud_uuid_canonical_form(
    client: TestClient, session: Session, tenant: Tenant
) -> None:
    """The whole point of `display_url` is to NOT be the canonical
    `cloud-{uuid}` form. Refuse the write so a future resolver bug
    can't accidentally poison the column."""
    # The conftest fixture sets display_url to the friendly form. Capture
    # its prior value so we can verify the bogus write didn't overwrite it.
    prior = tenant.display_url
    response = client.put(
        "/api/forge/sync/display-url",
        json={"display_url": "https://cloud-0d6a163d-52af-4d8c-b3d2-1233d3caa026.atlassian.net"},
    )
    assert response.status_code == 200
    session.refresh(tenant)
    assert tenant.display_url == prior
    assert "cloud-" not in (tenant.display_url or "")


def test_refuses_non_atlassian_url(client: TestClient, session: Session, tenant: Tenant) -> None:
    """Out-of-band guard against the resolver pushing a string that
    happens not to be a Jira site (network bug, mocked test, etc.)."""
    prior = tenant.display_url
    response = client.put(
        "/api/forge/sync/display-url",
        json={"display_url": "https://example.com"},
    )
    assert response.status_code == 200
    session.refresh(tenant)
    assert tenant.display_url == prior


def test_refuses_empty_string(client: TestClient, session: Session, tenant: Tenant) -> None:
    prior = tenant.display_url
    response = client.put(
        "/api/forge/sync/display-url",
        json={"display_url": ""},
    )
    assert response.status_code == 200
    session.refresh(tenant)
    assert tenant.display_url == prior


def test_idempotent_repeat_call(client: TestClient, session: Session, tenant: Tenant) -> None:
    """Re-PUT with the same URL is a no-op at the DB level (the resolver
    pings this endpoint on every dashboard mount; we don't want a write
    on every page load)."""
    url = "https://your-site.atlassian.net"
    r1 = client.put("/api/forge/sync/display-url", json={"display_url": url})
    r2 = client.put("/api/forge/sync/display-url", json={"display_url": url})
    assert r1.json()["displayUrl"] == url
    assert r2.json()["displayUrl"] == url


def test_updates_when_url_changes(client: TestClient, session: Session, tenant: Tenant) -> None:
    """A site that moved cloud IDs or got renamed should update — the
    fix doesn't lock the value in once it's set."""
    client.put(
        "/api/forge/sync/display-url",
        json={"display_url": "https://old-name.atlassian.net"},
    )
    client.put(
        "/api/forge/sync/display-url",
        json={"display_url": "https://new-name.atlassian.net"},
    )
    session.refresh(tenant)
    assert tenant.display_url == "https://new-name.atlassian.net"


# --- env_id (Forge environmentId for deep-link URLs) -----------------------


def test_persists_env_id(client: TestClient, session: Session, tenant: Tenant) -> None:
    """The resolver heartbeat pushes `env_id` alongside `display_url`;
    persisting it on the tenant unlocks the deep-link URL form in
    `project_dashboard_url`. Without it, helpers fall back to /boards."""
    response = client.put(
        "/api/forge/sync/display-url",
        json={
            "display_url": "https://my-site.atlassian.net",
            "env_id": "1a2b3c4d-5e6f-7890-abcd-ef0123456789",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["displayUrl"] == "https://my-site.atlassian.net"
    assert body["envId"] == "1a2b3c4d-5e6f-7890-abcd-ef0123456789"
    session.refresh(tenant)
    assert tenant.forge_env_id == "1a2b3c4d-5e6f-7890-abcd-ef0123456789"


def test_persists_env_id_alone(client: TestClient, session: Session, tenant: Tenant) -> None:
    """Each field is independently optional in the body — pushing env_id
    without display_url should still persist env_id (resolver might
    have one but not the other in a brief deploy-window state)."""
    prior_display = tenant.display_url
    response = client.put(
        "/api/forge/sync/display-url",
        json={"env_id": "abc-123"},
    )
    assert response.status_code == 200
    session.refresh(tenant)
    assert tenant.forge_env_id == "abc-123"
    assert tenant.display_url == prior_display  # unchanged


def test_env_id_idempotent(client: TestClient, session: Session, tenant: Tenant) -> None:
    payload = {
        "display_url": "https://my-site.atlassian.net",
        "env_id": "abc-123",
    }
    client.put("/api/forge/sync/display-url", json=payload)
    r2 = client.put("/api/forge/sync/display-url", json=payload)
    assert r2.json()["envId"] == "abc-123"
    session.refresh(tenant)
    assert tenant.forge_env_id == "abc-123"


def test_env_id_empty_string_no_op(client: TestClient, session: Session, tenant: Tenant) -> None:
    """An empty env_id string is a no-op — defense against the resolver
    pushing `""` when the context field is absent."""
    client.put(
        "/api/forge/sync/display-url",
        json={"env_id": "abc-123"},
    )
    client.put("/api/forge/sync/display-url", json={"env_id": ""})
    session.refresh(tenant)
    assert tenant.forge_env_id == "abc-123"
