# Publishing a Forge app to the Atlassian Marketplace

A step-by-step guide to listing a Forge app on the Atlassian Marketplace,
written for someone doing it the first time. It captures the operational
details that aren't obvious from Atlassian's docs — where each listing field
actually lives, what auto-publishes vs. what waits for review, and the version /
review-timing gotchas that are easy to get wrong.

Use your own values wherever you see a placeholder: `<YOUR_APP_ID>` (the Forge
app ARI in `forge-prod/manifest.yml`), `<your-vendor-id>`, `<your-space-name>`.

> **First, the biggest source of confusion — three different Atlassian portals:**
> - **developer.atlassian.com** — the Forge developer console. Owns your app and
>   your *Developer Space*.
> - **marketplace.atlassian.com/manage** — the vendor / Marketplace console. This
>   is where the **listing** lives and where you edit listing copy.
> - **partner.atlassian.com** — the separate Marketplace **Partner Program**
>   portal (Solution Partner / Cloud Fortified). It looks related but has
>   different access and surfaces. It is **not** where you manage your listing.

## What you'll need

| What | Where to find it |
|---|---|
| Forge app ARI (`<YOUR_APP_ID>`) | `forge-prod/manifest.yml` → `app.id` (set by `forge register`) |
| Vendor account ID (`<your-vendor-id>`) | `marketplace.atlassian.com/manage/vendors/<your-vendor-id>/details` |
| Developer Space name (`<your-space-name>`) | developer.atlassian.com → space switcher (top-left) |
| The admin email that publishes | Becomes the sole vendor admin until you add co-admins (vendor root → **Team**) |

**The chain must match end-to-end:** the vendor selected on the listing form must
equal the Developer Space the app is assigned to — otherwise the Forge-app
dropdown on `marketplace.atlassian.com/manage` shows "no Forge apps."

## First-time publish — order of operations (once per vendor)

A Forge app must live inside a **published Developer Space** before the listing
flow can even detect it.

1. **Create a Developer Space.** developer.atlassian.com → profile → Developer
   Console → space switcher (top-left) → **Create Developer Space**. Name it the
   same as your vendor (`<your-space-name>`).
2. **Assign the Forge app to that space.** If the app was registered before the
   space existed: space switcher → the app's current space → app list → overflow
   menu (`⋯`) on the app row → **Transfer app** → pick the destination space.
   *Gotcha:* requires Admin in **both** spaces, and transfers are blocked on the
   first two days of the month while invoices are pending.
3. **Publish the Developer Space.** developer.atlassian.com → the destination
   space → **Settings** → "Make public on Marketplace" → **Review & publish** →
   accept the Atlassian Marketplace Partner Agreement → **Accept & publish**. The
   publishing admin now gains Marketplace admin permissions on the linked vendor
   account.
4. **Submit the listing.** `marketplace.atlassian.com/manage/vendors/<your-vendor-id>/addons`
   → **Create new app** → **Forge app** → your app appears in the dropdown. This
   form covers app-root metadata (name, tagline, summary, categories, keywords).
   The longer-form copy is **per-version** — see the next section.

Atlassian's canonical references:
[Create a Developer Space](https://developer.atlassian.com/platform/forge/developer-space/create-developer-space/),
[Work with apps in a Developer Space](https://developer.atlassian.com/platform/forge/developer-space/developer-space-apps/),
[Publish a Developer Space](https://developer.atlassian.com/platform/forge/developer-space/publish-developer-space/),
[Building your presence on Marketplace](https://developer.atlassian.com/platform/marketplace/building-your-presence-on-marketplace/).

## Where each listing field actually lives

There is **no single "full description" field**. Listing copy is split across two
levels of the navigation hierarchy. Field structure (limits) is documented;
the navigation path below is what the live console actually shows.

**App root** — `marketplace.atlassian.com/manage/vendors/<your-vendor-id>/addons`
→ click your app. Tabs: **Versions / Details / Pricing / Privacy & Security /
Active installations / Downloads**.

| Field | Limit | Lives in |
|---|---|---|
| App name | n/a | App root → **Details** (once approved, changes are gated by review) |
| App tagline | 130 chars | App root → **Details** |
| App summary | 250 chars | App root → **Details** (shown in search results) |
| Categories + Keywords | n/a | App root → **Details** |
| **Highlights × 3** | see below | **Versions → [version] → Highlights tab** |
| **Hero image** | 960×600 px | **Versions → [version] → Highlights tab** (Hero & highlights format only) |
| **Media gallery** | images 1840×900, 220-char captions | **Versions → [version] → Media tab** |
| "More details" (long-form) | 1,000 chars | Versions → [version] → **Details tab** |
| Privacy & Security answers | n/a | Versions → [version] (per-version) |

- **Highlights:** each Highlight is **3 fields** (Title ≤50 chars, no trailing
  punctuation; Description ≤220; Image caption ≤220), paired with a **1840×900**
  image. The Highlights page has a format selector (Basic / Hero / Highlight /
  Hero & highlights); "Hero & highlights" adds the 960×600 Hero bound to the App
  tagline.
- **Per-version tabs** (click a version under Versions): **Details / Highlights /
  Media / Compatibility / Links**.

> **Gotchas here:**
> - On the app-root **Details** tab you'll see tagline / summary / categories /
>   keywords but **no "description" field** — the description isn't missing, it's
>   the per-version **"More details"** under Versions → [version].
> - There is **no "Features list" field.** The four prose slots are exhaustive:
>   tagline, App summary, 3 × Highlights, and "More details."
> - **Verification heuristic:** if you can't find an editor field, open the public
>   listing page — every prose field surfaces somewhere there. If the public page
>   shows a section you can't find in the editor, the field exists; keep looking.

Restructure a ~250-word description into 3 Highlights (≤220 chars each, each with
a screenshot) + one "More details" block (≤1,000 chars). A ~150-char short
description fits App summary; a headline fits the tagline.

## Forge versioning — you don't choose it, Forge does

Forge versions are `major.minor`, **not** three-part semver (Marketplace pads the
display to `6.0.0`; Forge stores `6.0`). Forge decides the bump from what changed
in the manifest:

| Change | Bump |
|---|---|
| Add/modify OAuth scopes | **Major** |
| Enable licensing (`licensing.enabled: true`, which exposes the Pricing tab) | **Major** |
| Add a `dynamic` **webtrigger** module (public URL surface) | **Major** |
| Add/remove providers, CSP options | **Major** |
| Resolver code, Custom UI bundle, event `trigger:`/`scheduledTrigger:`/`consumer:` modules, function config | **Minor** |

> **Gotcha:** "dynamic web triggers" in Atlassian's docs means the **`webtrigger`**
> module (a public URL), **not** event triggers like `avi:jira:*:issue` or
> `avi:forge:installed:app`. Adding event / scheduled / consumer modules has been
> **minor** in practice — an easy mis-read that causes a false "major bump"
> scare.

**Admin consent:** minor versions auto-apply to every install with no consent. A
**major** version is **not** applied to a site until its admin consents — so a
scope-only major doesn't break existing installs; they stay on the old major
until the admin accepts the upgrade prompt.

## What auto-publishes vs. what waits for review

After your **first** listing version is approved:

- **Every `forge deploy --environment production` becomes a public Marketplace
  version within minutes** — no "Make public" click for minor bumps. (Before
  first approval, versions stay Private until you click Make public on each.)
- **Release notes / Release summary** edits (Versions → [version] → Details)
  **auto-publish immediately**, no re-review.
- **Privacy & Security** edits (sub-processors, scope justification, data
  residency, certifications) go through the **Atlassian review queue** — typical
  SLA **1–3 business days**. You'll see a "pending approval" banner on submit.
- **Major** bumps (new scopes / licensing): treat as if they **might** gate on a
  listing re-review — Atlassian's docs don't say — and **schedule them off the
  critical path**.

Review timing to plan around:
- **First listing review:** Atlassian aims for a decision within about a week;
  budget **5–15 business days**.
- **Privacy & Security changes:** 1–3 business days.

## Freeze deploys during an active review

While any Marketplace listing review is in flight, **freeze both deploy paths**:

1. **No `forge deploy --environment production`.** Whether a new prod deploy
   overwrites, queues, or resets an in-review submission is **undocumented** —
   don't find out the hard way. If a Forge-side fix is genuinely urgent, email
   `developer-experience@atlassian.com` / `ecosystem@atlassian.com` and ask first.
2. **No pushes to any branch that triggers your deploy pipeline.** In this repo,
   `deploy.yml` runs on every push to `main` / `develop` / `feature/**` with no
   path filter — even a docs-only commit rebuilds the image and redeploys the
   backend. Reviewers exercise the **backend** and the **compliance URLs** filed
   in the listing form, so a regression during the review window can cause a
   rejection. Hold pushes until both review tracks approve.

## Gotcha: `jira:issuePanel` panels are hidden by default (FRGE-734)

Forge `jira:issuePanel` modules are **hidden by default** in the Jira issue view.
A user must click the **Apps** (hexagon) button on the right of the issue and
select the panel to make it appear; once added on an issue it persists for
everyone viewing that issue.

- Tracking issue: [FRGE-734](https://ecosystem.atlassian.net/browse/FRGE-734).
- A project admin can pre-enable a panel across a JQL-selected set of issues with
  a REST-API workaround Atlassian's developer community documented:
  [Enabling Jira Issue Panels by default](https://community.developer.atlassian.com/t/enabling-jira-issue-panels-by-default-a-guide-for-jira-administrators/91322).

Document this behavior for your users rather than shipping the workaround inside
the app — if you automate it, an Atlassian platform change becomes your bug.

## Screenshots for the listing

Highlights and Media images are **1840×900 px**; the Hero (if used) is
**960×600**. See [`docs/screenshots/`](screenshots/) for a ready-made 6-shot set
captured against the deterministic demo seed, with captions you can paste into
the listing form.
