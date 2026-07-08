"""Tests for the customer-facing URL helpers (ADR-0046 follow-up).

The bug this module exists to prevent: every callsite that builds a
customer-facing URL must go through `url_helpers` so the
`display_url`-first preference is single-sourced. The bug was caught when
the maintainer ran the backfill on `your-site`
and the completion email's "open it at" link landed on Atlassian's
"Page unavailable" page — root cause was `tenant.base_url`
(`cloud-{uuid}.atlassian.net`) being used directly.

Test surface:
- `tenant_site_url` resolution order (display_url > base_url > fallback).
- Trailing-slash hygiene.
- `ticket_url` form (verified against the documented `/browse/{key}`).
- `project_dashboard_url` form (verified against existing
  `forge_issue.py:73`).
- `settings_url` aliases `project_dashboard_url` (settings is a tab
  inside Jira Flow Intelligence project page; envId-gap caveat in module
  docstring).
- `pick_primary_project_key` resolution (most-active project, ties
  break alphabetically; None when tenant has no indexed issues).
- The bug-shape regression guard: a tenant with ONLY base_url set to
  `https://cloud-{uuid}.atlassian.net` produces URLs that route — i.e.
  the function does NOT silently use base_url even when display_url is
  NULL on a brand-new install (in practice this still falls through to
  base_url, but the install-path fix populates display_url so the
  fallback should not normally fire).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import Issue, Tenant
from app.services.url_helpers import (
    pick_primary_project_key,
    project_dashboard_url,
    settings_url,
    tenant_site_url,
    ticket_url,
)
from tests.conftest import make_tenant

# Real-looking values from the Forge runtime — the appId here is the
# UUID portion of `ari:cloud:ecosystem::app/<uuid>` per forge-prod/
# manifest.yml line 187. The envId is a representative UUID; Forge
# environment IDs are opaque, so anything UUID-shaped works for tests.
_APP_ID = "00000000-0000-0000-0000-000000000000"
_APP_ARI = f"ari:cloud:ecosystem::app/{_APP_ID}"
_ENV_ID = "1a2b3c4d-5e6f-7890-abcd-ef0123456789"


@pytest.fixture
def _settings_with_forge_app_id() -> object:
    """Patch get_settings() to return a Settings with forge_app_id set
    to the real-looking ARI. Used by the deep-link tests so they
    exercise the steady-state path. Tests that exercise the
    transitional fallback explicitly omit this fixture."""
    s = Settings(forge_app_id=_APP_ARI)
    with patch("app.services.url_helpers.get_settings", return_value=s):
        yield s


FIXED_NOW = datetime(2026, 6, 8, 12, 0, 0, tzinfo=UTC)


# --- tenant_site_url --------------------------------------------------------


def _bare_tenant(
    display_url: str | None,
    base_url: str | None,
    forge_env_id: str | None = None,
) -> Tenant:
    """Tiny in-memory Tenant for the pure-function tests. We don't persist
    these — they exist only to exercise the helper's branches."""
    return Tenant(
        client_key="test-tenant",
        cloud_id="test-cloud",
        forge_installation_id="test-tenant",
        display_url=display_url,
        base_url=base_url or "",
        forge_env_id=forge_env_id,
        product_type="jira",
        plan="free",
        enabled=True,
        installed_at=FIXED_NOW,
    )


def test_tenant_site_url_prefers_display_url() -> None:
    t = _bare_tenant(
        display_url="https://your-site.atlassian.net",
        base_url="https://cloud-abc.atlassian.net",
    )
    assert tenant_site_url(t) == "https://your-site.atlassian.net"


def test_tenant_site_url_falls_back_to_base_url_when_display_null() -> None:
    """Pre-install-path-fix state: display_url is NULL, base_url is the
    canonical `cloud-{uuid}` form. The helper still emits something
    (won't 404 with a malformed URL) but the customer hits Atlassian's
    "Page unavailable" page until the resolver heartbeat populates
    display_url. The install-path fix in this PR closes that gap on
    fresh mounts; this fallback remains for defense in depth."""
    t = _bare_tenant(
        display_url=None,
        base_url="https://cloud-abc-def.atlassian.net",
    )
    assert tenant_site_url(t) == "https://cloud-abc-def.atlassian.net"


def test_tenant_site_url_returns_fallback_when_both_null() -> None:
    t = _bare_tenant(display_url=None, base_url=None)
    assert tenant_site_url(t) == "https://atlassian.net"


def test_tenant_site_url_strips_trailing_slash() -> None:
    t = _bare_tenant(
        display_url="https://my-site.atlassian.net/",
        base_url=None,
    )
    assert tenant_site_url(t) == "https://my-site.atlassian.net"
    # Belt-and-suspenders for base_url too.
    t2 = _bare_tenant(display_url=None, base_url="https://cloud-x.atlassian.net/")
    assert tenant_site_url(t2) == "https://cloud-x.atlassian.net"


# --- ticket_url -------------------------------------------------------------


def test_ticket_url_uses_display_url_and_browse_form() -> None:
    t = _bare_tenant(
        display_url="https://your-site.atlassian.net",
        base_url="https://cloud-abc.atlassian.net",
    )
    assert ticket_url(t, "DEMO-1") == "https://your-site.atlassian.net/browse/DEMO-1"


def test_ticket_url_returns_none_when_key_missing() -> None:
    t = _bare_tenant(display_url="https://my-site.atlassian.net", base_url=None)
    assert ticket_url(t, None) is None
    assert ticket_url(t, "") is None


def test_ticket_url_does_not_emit_cloud_uuid_form_when_display_set() -> None:
    """Regression guard for the 2026-06-08 bug. Even if `cloud-{uuid}` is
    in base_url, a ticket URL must NOT include it when display_url is set."""
    t = _bare_tenant(
        display_url="https://my-site.atlassian.net",
        base_url="https://cloud-0d6a163d-52af-4d8c-b3d2-1233d3caa026.atlassian.net",
    )
    url = ticket_url(t, "JIRA-42")
    assert url is not None
    assert "cloud-" not in url, (
        f"ticket URL must not embed the canonical cloud-{{uuid}} form; got {url!r}"
    )
    assert "my-site.atlassian.net" in url


# --- project_dashboard_url --------------------------------------------------


@pytest.mark.usefixtures("_settings_with_forge_app_id")
def test_project_dashboard_url_constructs_deep_link_when_identifiers_populated() -> None:
    """Steady-state: tenant has forge_env_id + backend has forge_app_id →
    full Forge deep-link URL form per docs."""
    t = _bare_tenant(
        display_url="https://your-site.atlassian.net",
        base_url=None,
        forge_env_id=_ENV_ID,
    )
    assert project_dashboard_url(t, "DEMO") == (
        f"https://your-site.atlassian.net/jira/software/projects/DEMO/apps/{_APP_ID}/{_ENV_ID}"
    )


@pytest.mark.usefixtures("_settings_with_forge_app_id")
def test_project_dashboard_url_falls_back_to_boards_when_env_id_missing() -> None:
    """Transitional state: tenant created but resolver heartbeat hasn't
    populated forge_env_id yet. Verified-working /boards URL via the
    forge_issue.py:73 pattern — one Jira-side click from Jira Flow Intelligence.
    Window closes on next dashboard mount."""
    t = _bare_tenant(
        display_url="https://my-site.atlassian.net",
        base_url=None,
        forge_env_id=None,  # heartbeat hasn't fired yet
    )
    assert (
        project_dashboard_url(t, "DEMO")
        == "https://my-site.atlassian.net/jira/software/projects/DEMO/boards"
    )


def test_project_dashboard_url_falls_back_to_boards_when_app_id_missing() -> None:
    """Without forge_app_id config (Connect-era state), the URL still
    routes to a working page via /boards — no settings patching here
    means the default Settings.forge_app_id = "" branch fires."""
    t = _bare_tenant(
        display_url="https://my-site.atlassian.net",
        base_url=None,
        forge_env_id=_ENV_ID,  # tenant side populated
    )
    s = Settings(forge_app_id="")  # backend side empty
    with patch("app.services.url_helpers.get_settings", return_value=s):
        assert (
            project_dashboard_url(t, "DEMO")
            == "https://my-site.atlassian.net/jira/software/projects/DEMO/boards"
        )


@pytest.mark.usefixtures("_settings_with_forge_app_id")
def test_project_dashboard_url_falls_back_to_jira_root_when_project_missing() -> None:
    """If no project context, the customer at least lands on their Jira
    homepage rather than getting a malformed deep-link URL."""
    t = _bare_tenant(
        display_url="https://my-site.atlassian.net",
        base_url=None,
        forge_env_id=_ENV_ID,
    )
    assert project_dashboard_url(t, None) == "https://my-site.atlassian.net/jira"
    assert project_dashboard_url(t, "") == "https://my-site.atlassian.net/jira"


@pytest.mark.usefixtures("_settings_with_forge_app_id")
def test_project_dashboard_url_does_not_emit_cloud_uuid_form() -> None:
    """Regression guard for the 2026-06-08 bug — both the steady-state
    deep-link and the transitional fallback must avoid the canonical
    `cloud-{uuid}` form when display_url is set."""
    t = _bare_tenant(
        display_url="https://my-site.atlassian.net",
        base_url="https://cloud-0d6a163d-52af-4d8c-b3d2-1233d3caa026.atlassian.net",
        forge_env_id=_ENV_ID,
    )
    assert "cloud-" not in project_dashboard_url(t, "DEMO")


# --- settings_url -----------------------------------------------------------


def test_settings_url_aliases_project_dashboard_url() -> None:
    """Today Settings is a tab inside Jira Flow Intelligence's project page, reached
    via the project's Apps left-nav. Same URL; documented in url_helpers."""
    t = _bare_tenant(display_url="https://my-site.atlassian.net", base_url=None)
    assert settings_url(t, "DEMO") == project_dashboard_url(t, "DEMO")


# --- pick_primary_project_key ----------------------------------------------


def _make_issue(session: Session, tenant_id: str, issue_id: str, project_key: str) -> Issue:
    issue = Issue(
        tenant_id=tenant_id,
        id=issue_id,
        key=f"K-{issue_id}",
        project_key=project_key,
        current_status="Done",
        created_at=FIXED_NOW - timedelta(days=10),
        updated_at=FIXED_NOW,
    )
    session.add(issue)
    session.flush()
    return issue


def test_pick_primary_project_key_returns_most_populous(session: Session) -> None:
    tenant = make_tenant(session, client_key="url-test")
    # Project ABC has 3 issues, DEF has 1.
    _make_issue(session, tenant.client_key, "1", "ABC")
    _make_issue(session, tenant.client_key, "2", "ABC")
    _make_issue(session, tenant.client_key, "3", "ABC")
    _make_issue(session, tenant.client_key, "4", "DEF")
    assert pick_primary_project_key(session, tenant.client_key) == "ABC"


def test_pick_primary_project_key_breaks_ties_alphabetically(session: Session) -> None:
    tenant = make_tenant(session, client_key="url-test")
    _make_issue(session, tenant.client_key, "1", "ZZZ")
    _make_issue(session, tenant.client_key, "2", "AAA")
    # 1-1 tie — alphabetical wins.
    assert pick_primary_project_key(session, tenant.client_key) == "AAA"


def test_pick_primary_project_key_none_when_no_issues(session: Session) -> None:
    tenant = make_tenant(session, client_key="url-test")
    assert pick_primary_project_key(session, tenant.client_key) is None


def test_pick_primary_project_key_ignores_null_project_keys(session: Session) -> None:
    """Issues with NULL project_key (shouldn't happen, but defensive)
    don't drive the pick — they'd render as `/projects//boards` which
    is the exact malformed-URL footgun we want to avoid."""
    tenant = make_tenant(session, client_key="url-test")
    _make_issue(session, tenant.client_key, "1", "ABC")
    # Skip persisting a NULL-project issue — Issue.project_key is
    # nullable; create via the model directly.
    null_issue = Issue(
        tenant_id=tenant.client_key,
        id="2",
        key="K-2",
        project_key=None,
        current_status="Done",
        created_at=FIXED_NOW - timedelta(days=10),
        updated_at=FIXED_NOW,
    )
    session.add(null_issue)
    session.flush()
    assert pick_primary_project_key(session, tenant.client_key) == "ABC"


def test_pick_primary_project_key_scoped_to_tenant(session: Session) -> None:
    """Tenant A's most-populous project must not bleed into tenant B's
    pick. Pinned because the WHERE clause includes tenant_id."""
    a = make_tenant(session, client_key="tenant-a")
    b = make_tenant(session, client_key="tenant-b")
    _make_issue(session, a.client_key, "1", "AAA")
    _make_issue(session, b.client_key, "2", "BBB")
    assert pick_primary_project_key(session, a.client_key) == "AAA"
    assert pick_primary_project_key(session, b.client_key) == "BBB"


# --- Headline regression guard for the 2026-06-08 bug ----------------------


@pytest.mark.usefixtures("_settings_with_forge_app_id")
def test_no_helper_emits_canonical_cloud_uuid_form_when_display_set() -> None:
    """The named-destination helpers must never embed `cloud-{uuid}` in
    their output when display_url is set. This is the bug-class regression
    guard — the next person who adds a helper here has to write a test
    that exercises this guarantee, or the bug recurs.

    Exercises BOTH the steady-state deep-link path (with forge_env_id
    populated) AND the transitional fallback (without it). Both must
    avoid the canonical `cloud-{uuid}` form.
    """
    base_url = "https://cloud-0d6a163d-52af-4d8c-b3d2-1233d3caa026.atlassian.net"
    for env_id in (_ENV_ID, None):
        t = _bare_tenant(
            display_url="https://my-site.atlassian.net",
            base_url=base_url,
            forge_env_id=env_id,
        )
        urls_to_check = [
            tenant_site_url(t),
            ticket_url(t, "DEMO-1") or "",
            project_dashboard_url(t, "DEMO"),
            project_dashboard_url(t, None),
            settings_url(t, "DEMO"),
        ]
        for url in urls_to_check:
            assert "cloud-" not in url, (
                f"helper emitted canonical `cloud-{{uuid}}` form when "
                f"display_url was set (env_id={env_id!r}) — this is the "
                f"2026-06-08 bug shape. Offending URL: {url!r}"
            )
