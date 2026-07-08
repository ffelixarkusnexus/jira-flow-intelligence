"""TenantContext.terminal_statuses merge matrix (ADR-0038 / CLAUDE.md rule #10).

Covers all four cells of (override-on-terminal x independent_done_terminal_lists)
to lock the safe-default merge behavior in place. The regression these tests
guard against is the 2026-06-01 example-tenant footgun where a tenant added
"DONE" to done_statuses but not to terminal_statuses and got "Done is the
current bottleneck" with VERY HIGH CONFIDENCE.
"""

from __future__ import annotations

from app.core.config import Settings
from app.core.tenant_context import TenantContext
from tests.conftest import make_tenant


def test_default_tenant_merges_done_into_terminal(session):
    """Brand-new tenant: no overrides, toggle defaults to False (NULL in DB).
    Effective terminal = union of bundled defaults and bundled done."""
    settings = Settings()
    t = make_tenant(session)
    ctx = TenantContext(tenant=t, settings=settings)
    expected = sorted({*settings.terminal_statuses, *settings.done_statuses})
    assert ctx.terminal_statuses == expected


def test_override_terminal_with_toggle_off_still_merges_done(session):
    """The FOOTGUN case (Option 4 fix). Tenant overrode terminal_statuses
    without re-adding their custom Done values. Safe default merges done in
    anyway so "DEPLOYED is the bottleneck" can't happen."""
    settings = Settings()
    t = make_tenant(session)
    t.done_statuses = ["Done", "DEPLOYED"]
    t.terminal_statuses = ["Done", "Cancelled"]
    t.independent_done_terminal_lists = False
    ctx = TenantContext(tenant=t, settings=settings)
    # DEPLOYED is in done but NOT in the explicit terminal override.
    # With toggle off, it must still appear in effective terminal.
    assert "DEPLOYED" in ctx.terminal_statuses
    assert "Cancelled" in ctx.terminal_statuses


def test_toggle_on_keeps_lists_independent(session):
    """Advanced workflow (Done → Verified → Released). Tenant flips the
    toggle on AND sets explicit terminal that excludes Done. The override
    is authoritative — Done is bottleneck-eligible again."""
    settings = Settings()
    t = make_tenant(session)
    t.done_statuses = ["Done"]
    t.terminal_statuses = ["Released", "Cancelled"]
    t.independent_done_terminal_lists = True
    ctx = TenantContext(tenant=t, settings=settings)
    assert ctx.terminal_statuses == ["Released", "Cancelled"]
    assert "Done" not in ctx.terminal_statuses


def test_toggle_on_with_no_terminal_override_uses_settings_default_no_merge(session):
    """Toggle on, no explicit terminal override: returns bundled defaults
    as-is. Done is NOT auto-merged. Matches the toggle's promise of fully
    independent lists."""
    settings = Settings()
    t = make_tenant(session)
    t.done_statuses = ["Done", "CUSTOM_DONE"]
    t.terminal_statuses = None
    t.independent_done_terminal_lists = True
    ctx = TenantContext(tenant=t, settings=settings)
    # CUSTOM_DONE was added to done but should NOT appear in terminal
    # because the toggle says lists are independent.
    assert "CUSTOM_DONE" not in ctx.terminal_statuses
    assert ctx.terminal_statuses == list(settings.terminal_statuses)


def test_toggle_flip_back_to_off_re_engages_merge(session):
    """Tenant turns the toggle on, then later off again. The merge resumes
    cleanly. Guards against any sticky-state bug in the property logic."""
    settings = Settings()
    t = make_tenant(session)
    t.done_statuses = ["Done", "SHIPPED"]
    t.terminal_statuses = ["Released"]
    t.independent_done_terminal_lists = True
    ctx_on = TenantContext(tenant=t, settings=settings)
    assert "SHIPPED" not in ctx_on.terminal_statuses

    t.independent_done_terminal_lists = False
    ctx_off = TenantContext(tenant=t, settings=settings)
    assert "SHIPPED" in ctx_off.terminal_statuses
    assert "Released" in ctx_off.terminal_statuses
