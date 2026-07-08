"""Centralized customer-facing URL construction.

Bug class this module exists to close: helpers like the prior
`resend_service._dashboard_link` were reading `tenant.base_url`
(Atlassian's opaque canonical `cloud-{uuid}.atlassian.net` form, which
does NOT route to the user-facing site) instead of `tenant.display_url`
(the user-facing site URL). The asymmetry was in plain sight — the
adjacent `_site_name` helper correctly preferred `display_url` — but it
slipped past review and shipped to production. Every customer who
clicked an alert ticket link, an "open the dashboard" CTA, or a
backfill-complete email link landed on Atlassian's "Page unavailable"
page until the 2026-06-08 bug was caught.

The fix has two parts:

1. **Centralize** every customer-facing URL construction here, behind
   named-destination helpers (`project_dashboard_url`, `ticket_url`,
   `settings_url`). Direct concatenation of `tenant.base_url` or
   `tenant.display_url` outside this module is the bug-class
   re-introduction the next person will quietly make; force callers
   through typed helpers so they can't.

2. **Prefer `display_url` over `base_url`** in the underlying site-URL
   resolution. `display_url` is the friendly form
   (`my-site.atlassian.net`); `base_url` is the canonical
   `cloud-{uuid}` form which doesn't route to the user-facing site.

Deep-link URL form per Atlassian's documented
`/jira/{projectType}/projects/{key}/apps/{appId}/{envId}` pattern
(developer.atlassian.com/platform/forge/manifest-reference/modules/
jira-project-page/). Two identifiers needed:

- `appId` — the UUID portion of the app ARI from the Forge manifest
  (`ari:cloud:ecosystem::app/<uuid>`). Static per app version. Lives
  in backend `Settings.forge_app_id`.
- `envId` — Forge's per-environment identifier
  (`@forge/bridge` FullContext `environmentId`). Not carried in the
  FIT, so it has to be captured from the resolver context and pushed
  to the backend; persisted on `Tenant.forge_env_id` via the existing
  `PUT /api/forge/sync/display-url` endpoint (extended in the same
  PR as this module's deep-link landing).

Transitional fallback: when either identifier is missing, the helpers
emit the project's Jira boards URL (`/jira/software/projects/{key}/
boards`) — verified-working route via `forge_issue.py:73` but one
Jira-side click shy of Jira Flow Intelligence. Fallback exists only for the
window between a tenant being created and its first dashboard mount
(when the resolver heartbeat populates the column). After that,
every email link goes directly to Jira Flow Intelligence.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Issue, Tenant

# The Forge project-page module key from forge-prod/manifest.yml line 3.
# Static — if we ever rename the module, this constant + the manifest
# entry move together (with a major version bump per the runbook's
# Forge versioning section, since module-key changes are user-visible).
# NOTE: Atlassian's documented deep-link URL form
# `/jira/software/projects/{key}/apps/{appId}/{envId}` does NOT include
# the module key — the appId+envId pair identifies the projectPage
# module uniquely for the install. Module key constant is kept here for
# completeness, not because the URL needs it.
_PROJECT_PAGE_MODULE_KEY = "flow-intelligence-dashboard"


def _app_id_from_settings() -> str | None:
    """Extract the appId UUID portion from `forge_app_id` setting.

    `forge_app_id` is the full Forge ARI
    (`ari:cloud:ecosystem::app/<uuid>`); the deep-link URL needs only
    the trailing UUID. Returns None when the setting is empty (Connect-
    era state — Forge auth not configured) so the helpers fall through
    to the transitional URL form.
    """
    forge_app_id = get_settings().forge_app_id
    if not forge_app_id:
        return None
    # ARIs are slash-delimited; the trailing segment is the app UUID.
    # An ARI without `/` (malformed) would already break FIT auth long
    # before reaching this code, so no defensive parse — last segment
    # is enough.
    return forge_app_id.rsplit("/", 1)[-1] or None


# Atlassian's last-ditch fallback. Shouldn't normally render; the
# install-path bug-fix in this PR populates `display_url` on every
# Forge install, so by the time a tenant gets to the email-sending
# code path both `display_url` and `base_url` will be set. Kept for
# defense in depth.
_FALLBACK_SITE_URL = "https://atlassian.net"


def tenant_site_url(tenant: Tenant) -> str:
    """The user-facing Atlassian site URL for the tenant.

    Resolution order:
      1. `tenant.display_url` — the friendly form (e.g.
         `https://your-site.atlassian.net`). Always prefer this.
      2. `tenant.base_url` — Atlassian's opaque canonical form
         (`https://cloud-{uuid}.atlassian.net`). Used only as a last
         resort; this URL does NOT route to the user-facing site, so
         falling through to it means the customer will hit Atlassian's
         "Page unavailable" page on click. The install-path fix in this
         PR populates `display_url` on every Forge install so this
         fallback should not normally fire.
      3. `https://atlassian.net` — defense-in-depth fallback that at
         least doesn't 404.

    Returns the bare site URL with the trailing slash stripped, ready
    for concatenation with route paths by the named-destination
    helpers below. Callers SHOULD NOT use this directly to build
    customer-facing URLs — go through the named helpers so the
    routing logic stays in one place.
    """
    if tenant.display_url:
        return tenant.display_url.rstrip("/")
    if tenant.base_url:
        return tenant.base_url.rstrip("/")
    return _FALLBACK_SITE_URL


def ticket_url(tenant: Tenant, ticket_key: str | None) -> str | None:
    """One-click deep-link to a specific Jira ticket.

    Returns: `{site}/browse/{TICKET-123}`. Verified by the existing
    `WipAgingChart.tsx` click-through (`/browse/{key}` resolves on
    every Jira Cloud site).

    Use in:
      - Alert ticket-links (`alert_messages.py` ticket buttons)
      - Any "open the ticket" CTA

    Returns None when `ticket_key` is missing — matches the prior
    `_ticket_url` signature so callers can render-or-omit.
    """
    if not ticket_key:
        return None
    return f"{tenant_site_url(tenant)}/browse/{ticket_key}"


def project_dashboard_url(tenant: Tenant, project_key: str | None) -> str:
    """One-click deep-link to Jira Flow Intelligence on the named project.

    URL form: `{site}/jira/software/projects/{KEY}/apps/{appId}/{envId}`
    per Atlassian's documented Forge URL pattern (see module docstring).
    Lands the user directly inside Jira Flow Intelligence on the project the
    email is about — the "Uber drops you at the destination, not blocks
    away" standard.

    Resolution:
      1. If `tenant.forge_env_id` AND `forge_app_id` (from Settings)
         are both populated → deep-link form. This is the steady state
         after the dashboard resolver heartbeat has fired once for the
         tenant.
      2. Transitional fallback to `{site}/jira/software/projects/{KEY}/
         boards` when either identifier is missing — only fires in the
         small window between tenant creation and first dashboard
         mount. Verified-working route via `forge_issue.py:73`; one
         Jira-side click shy of Jira Flow Intelligence.
      3. `{site}/jira` when project_key is missing.

    Use in:
      - Backfill-complete notification (the "open it at" link)
      - Alert "open the dashboard" CTAs
      - Failure-digest "review destinations" CTA
    """
    site = tenant_site_url(tenant)
    if not project_key:
        return f"{site}/jira"
    app_id = _app_id_from_settings()
    env_id = tenant.forge_env_id
    if app_id and env_id:
        return f"{site}/jira/software/projects/{project_key}/apps/{app_id}/{env_id}"
    # Transitional fallback — see (2) above. Should not normally fire
    # past the first dashboard mount of any given install.
    return f"{site}/jira/software/projects/{project_key}/boards"


def settings_url(tenant: Tenant, project_key: str | None) -> str:
    """Link to Jira Flow Intelligence's Settings page for the named project.

    Same destination as `project_dashboard_url` today — Settings is a
    tab within the Jira Flow Intelligence project page, reached through the
    "Apps" left-nav from the project's Jira landing page. The same
    `envId`-gap caveat applies.

    Use in:
      - Backfill-failure email (retry path)
      - Any "reconfigure" CTA
    """
    return project_dashboard_url(tenant, project_key)


def pick_primary_project_key(db: Session, tenant_id: str) -> str | None:
    """Best guess at "the project the customer cares about right now."

    Used by tenant-scoped notifications (backfill complete / failure /
    cap-reached, failure digest) that need to render a project-scoped
    dashboard URL but don't have a specific project from the trigger
    context. Resolution: the project with the most ingested issues
    (`Issue.project_key GROUP BY project_key ORDER BY count DESC`).
    Ties break alphabetically for determinism across repeated runs.

    Returns `None` when the tenant has no indexed issues yet — caller
    passes that through to `project_dashboard_url` which falls back to
    the tenant's `/jira` root (better than a 404 from a malformed
    `/projects//boards` URL).

    Not in `Tenant.preferred_project_key` because (a) we don't have a
    "set primary project" UI today, (b) the most-issues heuristic is
    actually a reasonable answer most of the time, and (c) preferences
    can drift if we ever add the UI — derived-from-data is
    self-correcting in a way a stored field isn't.
    """
    row = db.execute(
        select(Issue.project_key, func.count(Issue.id).label("n"))
        .where(Issue.tenant_id == tenant_id, Issue.project_key.is_not(None))
        .group_by(Issue.project_key)
        .order_by(func.count(Issue.id).desc(), Issue.project_key.asc())
        .limit(1)
    ).first()
    return row[0] if row else None
