# Runbook

How to operate this system locally and (eventually) in production.

The app is distributed as a [Forge](https://developer.atlassian.com/platform/forge/) Custom UI app per [ADR-0019](adr/0019-pivot-to-forge.md). The Custom UI is served by Atlassian's CDN and calls a FastAPI backend on AWS App Runner via `@forge/bridge` `requestRemote`; the backend validates the Forge Invocation Token against Atlassian's JWKS.

**Verification discipline for shipping work:** the concrete per-change-type checklists live in [`definition-of-done.md`](definition-of-done.md); work-completion handoffs (to the reviewer / maintainer) use [`handoff-template.md`](handoff-template.md). Both are referenced by [CLAUDE.md rule #12](../../CLAUDE.md). The operational runbook below is what you read to *do* the work; those docs are what you read to confirm it's *done*.

## Local development

### First run

```bash
uv sync                                           # install backend deps
cd frontend && npm install && cd ..               # install frontend deps

# Apply DB migrations (creates tables for the multi-tenant schema)
DATABASE_URL=sqlite:///backend/data/flow.db PYTHONPATH=backend uv run alembic upgrade head

# Seed synthetic Jira-shaped data (also creates a `demo-tenant` row)
PYTHONPATH=backend uv run python -m app.seeds.demo
```

### Day-to-day

Two terminals:

```bash
# Terminal 1 — backend
PYTHONPATH=backend uv run uvicorn app.main:app --reload

# Terminal 2 — frontend
cd frontend
BACKEND_URL=http://127.0.0.1:8000 npm run dev
```

Dashboard at <http://localhost:3000/dashboard>. API docs at <http://localhost:8000/docs>.

### Reset the local database

```bash
rm -f backend/data/flow.db
DATABASE_URL=sqlite:///backend/data/flow.db PYTHONPATH=backend uv run alembic upgrade head
PYTHONPATH=backend uv run python -m app.seeds.demo
```

### Database migrations (Alembic)

Schema lives in `backend/app/db/models.py`; migrations live in `backend/alembic/versions/`.

```bash
# After changing models, autogenerate a migration
DATABASE_URL=sqlite:///backend/data/flow.db PYTHONPATH=backend uv run alembic revision \
  --autogenerate -m "describe the change"

# Review the generated file before applying — autogen is good, not perfect
$EDITOR backend/alembic/versions/<latest>.py

# Apply
DATABASE_URL=... PYTHONPATH=backend uv run alembic upgrade head

# Rollback one step
DATABASE_URL=... PYTHONPATH=backend uv run alembic downgrade -1
```

`UTCDateTime` columns auto-render as `sa.DateTime()` in migrations (the type decorator only affects Python-side conversion). See `backend/alembic/env.py::_render_item`.

**Do not** call `Base.metadata.create_all` outside tests — that bypasses Alembic and drifts your schema from migration history.

## Connecting to a real Jira instance

1. Create an Atlassian API token at <https://id.atlassian.com/manage-profile/security/api-tokens>.
2. Copy `.env.example` to `.env` and fill in:
   ```
   JIRA_BASE_URL=https://your-org.atlassian.net
   JIRA_EMAIL=you@example.com
   JIRA_API_TOKEN=...
   JIRA_JQL=project IN (ABC, DEF) ORDER BY updated DESC
   ```
3. Trigger a sync (requires a valid Atlassian JWT — see "How do I hit a protected endpoint with `curl` from my laptop?" below):
   ```bash
   curl -X POST -H "Authorization: JWT $TOKEN" http://localhost:8000/api/sync
   ```
4. The first sync may take a while depending on issue count. Subsequent syncs reprocess whatever JQL returns; **incremental sync is not yet implemented** (see "Deferred work").

## CI / Deploy architecture

Two workflows under `.github/workflows/`, intentionally aligned. **Same path-detection design across both**: every job in every workflow gates on whether the commit actually touched the area that job exercises.

### Path-detection model

Every workflow run starts with a `changes` job that diffs `HEAD~1..HEAD` and sets boolean outputs per area (`backend`, `infra`, `forge`, plus `backend_image` on the Deploy side). Subsequent jobs depend on `changes` via `needs:` and gate on `if: needs.changes.outputs.<area> == 'true'`. Both `ci.yml` and `deploy.yml` use the same shell-based detector — the path patterns must be kept in sync between the two; a divergence is the cue to extract the detector into a composite action.

Special clauses:
- **Workflow self-change forces everything true.** A change to `ci.yml` forces all CI jobs to run on that commit; a change to `deploy.yml` forces a full deploy. This validates the new workflow shape end-to-end on the commit that introduces it.
- **Fail-safe on missing `HEAD~1`** (shallow checkout, first commit, unusual history): force everything true rather than risk silently skipping a real test cycle.
- **`workflow_dispatch` on Deploy** forces everything true (ops override).

### Layered with workflow-trigger `paths-ignore`

`ci.yml`'s `on:` block has `paths-ignore` for paths that can never affect any CI job (`**.md`, `docs/**`, `LICENSE`, `.gitignore`, `.github/dependabot.yml`). For those, the workflow doesn't start at all — cheapest exit. For everything else, the `changes` job decides per-job. Because Deploy fires via `workflow_run` after CI, skipping CI also skips Deploy.

### CI jobs

| Job | Triggers on | Steps |
|-----|-------------|-------|
| `changes` | always (when CI fires) | diff `HEAD~1..HEAD`, set outputs |
| `backend` (matrix py3.12 / py3.13) | `backend/**`, `pyproject.toml`, `uv.lock` | ruff check, ruff format --check, mypy, pytest --cov (gate 80%) |
| `backend-postgres` | same as `backend` | Postgres RLS smoke (real PG service container) |
| `forge` | `forge-prod/{frontend/src/, src/, manifest.yml, package*.json, tsconfig*.json}` | resolver tsc, Custom UI typecheck + vite build, **Vitest + coverage** (ADR-0039; per-file 80% gate on `src/lib/{duration,alertGrouping,format}.ts`) |
| `infra` | `infra/**` | ruff, ruff format, mypy, pytest (CDK assertions) |

### Pre-push pre-flight (mandatory)

A GitHub-Actions-side validator that local toolchains (Python's `yaml.safe_load`, eyeballing the YAML) do NOT replace. Skipping it risks a failed CI run + a follow-up fix-commit cycle:

- **`actionlint .github/workflows/<file>.yml`** — before pushing any workflow change. Catches duplicate keys (which YAML allows but GitHub rejects), invalid step shapes, undefined `needs:` references, deprecated action versions. Surfaced 2026-06-03 when an Edit ate an `infra:` job-key line and `yaml.safe_load` accepted the resulting double-keys silently while GitHub returned "This run likely failed because of a workflow file issue."

Install via `brew install actionlint`. Same args CI uses (kept aligned in the workflow file's args block + this runbook entry).

A dependabot.yml-only commit: workflow trigger skipped via `paths-ignore` → zero runs. A backend-only commit: `changes` + `backend` matrix + `backend-postgres` run; `forge`, `infra` skip. A forge-only commit: `changes` + `forge` (incl. Vitest) run; others skip.

### Deploy jobs

Triggered via `workflow_run` after CI succeeds (or manual `workflow_dispatch`). Same `changes` shape, plus a `resolve` job that maps branch → env. Deploy jobs:

| Job | Triggers on | What it does |
|-----|-------------|--------------|
| `resolve` | always (gated on CI success) | branch → env mapping (`main` → prod, `develop` → staging, otherwise dev); resolves SHA from `workflow_run.head_sha` |
| `changes` | always | same detector as CI |
| `deploy` | `backend_image` or `infra` | conditional steps for ECR, image build+push, cdk deploy, smoke test |
| `forge_deploy_dev` | `forge` | `npm run tsc` + `npm run build` + `forge deploy --environment development --non-interactive` (forge usage-analytics set explicitly because `--non-interactive` requires it) |

Forge production deploys (`forge deploy --environment production`) deliberately stay manual — a push to main should not auto-publish to customers. See **Forge versioning** section below for the deploy semantics.

### Why this shape

The earlier shape was asymmetric — Deploy had path-awareness (added after a multi-week incident where every push fired a full backend image rebuild + 4-stack CDK redeploy regardless of what changed), but CI still ran all 5 jobs on every non-doc commit. Two systems thinking about change-detection differently. The unified shape closes that drift: same detector, same booleans, same `if:`-gating pattern on both sides; new contributors see one model instead of two.

## Transactional email

Dual-path delivery as of ADR-0040 (2026-06-03 — AWS SES production access was denied; Resend handles customer-facing email, SES retained for the maintainer-self path).

### Which path goes through which service

| Path | Recipient class | Service | Code |
|---|---|---|---|
| Alert delivery (ADR-0037) | Customer admin | Resend | `resend_service.send_alert_email` |
| Backfill completion / failure / cap-reached (ADR-0033) | Customer admin | Resend | `resend_service.fire_terminal_state_email` → `send_backfill_*_email` |
| 24h alert-delivery failure digest (ADR-0037) | Customer admin | Resend | `resend_service.send_failure_digest_email` |

### Adding a new email path

**Customer-facing email goes through Resend.** Every external recipient — customer admins, end users — is a Resend send. The implementation lives in `resend_service.py`; a new path is a new function there plus its caller.

**Why Resend, not SES:** SES was denied production access and is stuck in sandbox, which can only deliver to verified identities — the problem ADR-0040 exists to solve. Reaching for SES on a customer-facing path would either silently fail in sandbox or appear to work for a few pre-verified identities and then fail when real customer traffic hits it. External recipient → Resend, every time.

### Where the Resend API key lives

CDK provisions a dedicated `sm.Secret` per environment at the human-readable path `flow-intelligence/{env}/resend_api_key` (separate from `app_secrets` per ADR-0040 — vendor isolation for rotation + blast-radius separation). The backend reads the secret value at startup via `boto3.secretsmanager.get_secret_value(SecretId=RESEND_API_KEY_SECRET_ARN)` and configures the `resend` SDK once.

**Writing the key value (one-time, per environment, operator action):**

```bash
aws secretsmanager put-secret-value \
  --secret-id "flow-intelligence/prod/resend_api_key" \
  --secret-string "re_xxxxxxxxxxxxxxxxxxxxxxxx" \
  --region us-east-1
```

Or via AWS Console: Secrets Manager → `flow-intelligence/prod/resend_api_key` → Retrieve secret value → Edit → paste the key from `resend.com/api-keys` → Save.

The backend fails closed at startup if the secret is empty or missing — `resend_service.ResendConfigError` raises and the customer-facing email send returns False with a logged warning. If you see `resend_send_failed_config` in CloudWatch Logs, that's the secret-missing signal.

### Rotating the Resend key

1. Generate a new key at `resend.com/api-keys` (the dashboard lets you have multiple keys active simultaneously — generate the new one before revoking the old).
2. `aws secretsmanager put-secret-value` (command above) with the new value.
3. App Runner does not pick up the secret value on its own. **Bounce the backend service to re-fetch:** AWS Console → App Runner → `flow-backend-prod` → Deploy. The redeploy uses the same image tag; only the in-process `resend.api_key` reload happens.
4. Confirm a real send works (e.g. trigger a backfill completion email on a dev tenant, or check the Resend dashboard's outgoing-mail log for new sends after the redeploy).
5. Revoke the old key at `resend.com/api-keys`.

### Reading Resend dashboard logs

`resend.com/emails` shows every send: recipient, status (Delivered / Bounced / Complained / Failed), latency, message-id. Filter by date or by sender identity. The per-send `resend.send.count` log line in CloudWatch Logs is the cross-reference if you need to correlate a Resend dashboard entry back to which customer / which alert fire produced it.

### Bounce handling

Resend auto-suppresses recipients that hard-bounce; subsequent sends to those addresses are silently dropped at the Resend layer (no error returned to the backend). Hard-bounce reasons (invalid mailbox, domain doesn't exist) are surfaced in the Resend dashboard's email detail view. **If a customer reports "I'm not getting backfill emails":** first place to look is `resend.com/emails`, filter by their domain, check whether their address is on the suppression list. Remove from the suppression list via the dashboard if the customer fixed the underlying issue (typo, MX record correction, etc.).

Soft bounces (full mailbox, transient SMTP error) are retried by Resend automatically; no manual intervention needed.

### Free-tier cap monitoring (lightweight, manual)

Resend free tier: **3,000 sends / month, 100 / day**. Current projected volume sits well below at typical scale. Monitoring plan:

- **Manual monthly check (first of each month):** maintainer logs into `resend.com/usage`, confirms month-to-date is well under 3,000. Takes 30 seconds.
- **Per-send breadcrumb:** every successful `resend_service._send_email` emits a `resend.send.count` structured log line in CloudWatch Logs. CloudWatch Logs Insights query for a quick volume estimate:

  ```
  fields @timestamp, @message
  | filter @message like /resend\.send\.count/
  | stats count() by bin(1d)
  ```

- **Upgrade trigger:** if steady-state monthly volume crosses **1,000/mo**, build a CloudWatch alarm on the `resend.send.count` log-metric-filter — trip at 2,500 to give a buffer before the 3,000 cap. ADR-0040 §Consequences names this explicitly as deferred-until-volume; building it today would alert on a counter that reads near-zero (premature plumbing per CLAUDE.md).

### Local dev / testing

- **Tests**: mock `resend.Emails.send` via `unittest.mock.patch`. Test seam: `resend_service._set_initialized_for_tests(True)` to bypass the Secrets-Manager fetch. Mirrors the SES test pattern; see `backend/tests/test_resend_service.py`.
- **Local dev without Resend credentials:** set `RESEND_DRY_RUN=1`. Every send becomes a logged no-op that still returns True; useful for running the backend locally against a dev tenant without burning your daily quota or hitting the network. Tests should NOT rely on dry-run — they should use the mock, which is deterministic.

### One-time AWS setup (per environment)

Before the first deploy, an operator must:

1. Pick an AWS account; bootstrap CDK in the target region:
   ```bash
   cd infra
   AWS_ACCOUNT_ID=... AWS_REGION=us-east-1 \
     uv run cdk bootstrap aws://$AWS_ACCOUNT_ID/$AWS_REGION
   ```
2. Create an IAM role for GitHub OIDC. Trust policy:
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Principal": {"Federated": "arn:aws:iam::ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com"},
       "Action": "sts:AssumeRoleWithWebIdentity",
       "Condition": {
         "StringEquals": {"token.actions.githubusercontent.com:aud": "sts.amazonaws.com"},
         "StringLike": {"token.actions.githubusercontent.com:sub": "repo:OWNER/REPO:ref:refs/heads/*"}
       }
     }]
   }
   ```
   Attach permissions sufficient for ECR, App Runner, RDS, Secrets Manager, IAM, CloudFormation, CloudWatch. Tighten before public Marketplace listing.
3. In GitHub repo Settings → Environments, create `dev`, `staging`, `prod`. For each:
   - Vars: `AWS_ACCOUNT_ID`, `AWS_REGION`
   - Secret: `AWS_DEPLOY_ROLE_ARN`
4. (Optional, recommended for prod) Enable required reviewers via environment protection rules.
5. **Forge credentials (one-time, repo-level secrets — not per-env).** The Deploy workflow's `forge_deploy_dev` job auto-deploys Custom UI + resolver changes to the Forge **development** environment. It needs:
   - `FORGE_EMAIL` — the Atlassian account email that owns the Forge app (currently `alerts@example.com`).
   - `FORGE_API_TOKEN` — an Atlassian API token generated at https://id.atlassian.com/manage-profile/security/api-tokens. The same token works for both Forge dev and prod environments; the workflow only uses it for dev.

   Add via GitHub UI (Settings → Secrets and variables → Actions → New repository secret) or via CLI:
   ```bash
   gh secret set FORGE_EMAIL --body "alerts@example.com"
   gh secret set FORGE_API_TOKEN  # prompts for value
   ```

   Without these secrets, `forge_deploy_dev` fails on the `forge deploy` step with an auth error. Forge **production** deploys remain manual (`cd forge-prod && forge deploy --environment production` from the maintainer's terminal) — a push to main intentionally does not auto-publish to customers.

After this, pushing to `feature/*` deploys to dev automatically.

### Forge prod deploys (Custom UI + resolver)

The Forge app lives in `forge-prod/` — one tree, deployed to both `development` and `production` environments via `forge deploy --environment {development|production}`. (The `forge/` scaffold from Phase 2 was deleted 2026-05-27; the "prod" suffix is now historical.) Forge deploys are **not** part of the GitHub Actions deploy pipeline. They're manual via the Forge CLI:

```
cd forge-prod
npm run tsc                           # rebuild dist/ from src/resolvers/*.ts
cd frontend && npm run build && cd .. # rebuild static/main/ from frontend/src/
forge deploy --environment production
```

**Both `npm run tsc` and `npm run build` are mandatory before `forge deploy`** — Forge packages whatever is in `dist/` (resolver) and `static/main/` (Custom UI) without rebuilding either. A deploy that skips `tsc` will silently ship a stale resolver while reporting success and bumping the version — several no-op resolver versions have shipped this way before the mistake surfaced.

After deploy, the Forge runtime auto-upgrades all installs that don't require new permissions. Manifests that add `permissions.scopes` or `external.fetch` entries trigger a one-time admin re-grant prompt instead.

## Atlassian Marketplace publishing

### Production identifiers (the things you'll need to look up later)

| What | Value | Source / how to verify |
|---|---|---|
| Vendor account ID | `<your-vendor-id>` | https://marketplace.atlassian.com/manage/vendors/&lt;your-vendor-id&gt;/details |
| Vendor admin account | `alerts@example.com` | receives Atlassian Developer-and-Marketplace-Support email (approval notifications, etc.). The publishing admin is the sole vendor admin until co-admins are added. To add another admin: vendor root → **Team** tab. |
| Developer Space name | `Example` | developer.atlassian.com → space switcher (top-left) |
| Forge app ARI | `ari:cloud:ecosystem::app/00000000-0000-0000-0000-000000000000` | `forge-prod/manifest.yml` `app.id` |
| Forge internal app name | `flow-intelligence-prod` | what `forge register` set; the *display title* "Jira Flow Intelligence" comes from `manifest.yml` `title:` |

The vendor-name → space-name → forge-app-ARI chain has to match end-to-end: the **Vendor selected on the listing form must equal the Developer Space the app is assigned to**, otherwise the Forge-app dropdown on `marketplace.atlassian.com/manage` returns "no Forge apps".

**Host warning — do not confuse two different Atlassian portals.** `marketplace.atlassian.com/manage/vendors/<id>/` is the **vendor / app management** console (this is the one you want for listing edits). `partner.atlassian.com` is the separate **Marketplace Partner Program** portal (Solution Partner / Cloud Fortified program admin). They look superficially related but have different access controls and different surfaces; `alerts@example.com` is admin on the former, not necessarily on the latter.

### How a Forge app gets onto the Atlassian Marketplace

The flow lives in **two separate consoles** — `developer.atlassian.com` (the Forge developer console) for app and space ownership, and `marketplace.atlassian.com/manage` (the vendor / Marketplace console) for the listing itself. The non-obvious part: a Forge app must be inside a *published* Developer Space before the listing flow can detect it.

Order of operations, only needed once per vendor:

1. **Create a Developer Space** at developer.atlassian.com → profile → Developer Console → space switcher (top-left) → **Create Developer Space**. Name it the same as the vendor (`Example` here).
2. **Transfer the existing Forge app to that space** (only relevant if the app was registered before the space existed). Switch the space switcher to wherever the app currently lives → app list → overflow menu (`⋯`) on the app's row → **Transfer app** → pick destination space. Caveats: requires Admin in both spaces; blocked on the first two days of the month when invoices are pending.
3. **Publish the Developer Space** at developer.atlassian.com → switch to the destination space → **Settings** → "Make public on Marketplace" section → **Review & publish** → tick the Atlassian Marketplace Partner Agreement → **Accept & publish**. After this the publishing admin gains Marketplace admin permissions on the linked vendor account. **Capture which email did this in the production-identifiers table above — it's the sole vendor admin until co-admins are added.**
4. **Submit the listing** at `marketplace.atlassian.com/manage/vendors/<vendor-id>/addons` → **Create new app** → **Forge app** option → the app appears in the dropdown. The form here covers app-root metadata (name, tagline, app summary, categories, keywords). The longer-form copy (Highlights, screenshots, "More details") is **per-version** — see "Listing copy fields" below.

### Atlassian docs — canonical sources

- [Create a Developer Space](https://developer.atlassian.com/platform/forge/developer-space/create-developer-space/)
- [Work with apps in a Developer Space](https://developer.atlassian.com/platform/forge/developer-space/developer-space-apps/) (this is where the Transfer-app flow is documented)
- [Publish a Developer Space to the Atlassian Marketplace](https://developer.atlassian.com/platform/forge/developer-space/publish-developer-space/)
- [Distribute your apps](https://developer.atlassian.com/platform/forge/distribute-your-apps/) (sharing-link distribution alternative; not used here, but documents the constraint that "apps with a license in the manifest can't be shared via installation link")
- [Building your presence on Marketplace](https://developer.atlassian.com/platform/marketplace/building-your-presence-on-marketplace/) (listing copy field structure — see next subsection)

### Release notes per version

See [`docs/engineering/release-process.md`](release-process.md) for the consolidated release process policy (version-bump rules, CHANGELOG-to-release-notes derivation, PR-time discipline). This runbook subsection covers the operational mechanics of pasting approved release notes into the Atlassian Partner Console.

As a standard procedure, each Forge production deploy that surfaces a customer-visible change gets release notes filed in the Atlassian Partner Console within ~24h. Surface the customer-visible changes from the CHANGELOG, draft the customer-facing copy, review it, and paste the approved text into the partner console. Per-version Release notes / Release summary fields live at App root → Versions → [version] → Details tab; they auto-publish (no Atlassian re-review).

Versions before 6.3.0 are intentionally not backfilled. Going forward every customer-visible deploy populates.

### Listing copy fields — where each field actually lives in the UI

There is **no single "full description" field**. Listing copy is split across multiple fields at two different levels of the navigation hierarchy. **Field structure** (character limits, what each field is for) is documented in [Building your presence on Marketplace](https://developer.atlassian.com/platform/marketplace/building-your-presence-on-marketplace/). **Navigation path** is observed 2026-05-25 by clicking through the live partner console — Atlassian's docs do not specify it.

**App root** — `marketplace.atlassian.com/manage/vendors/<your-vendor-id>/addons` → click your app. The app-root tabs are: **Versions / Details / Pricing / Privacy & Security / Active installations / Downloads**.

| Field | Limit | Lives in | Notes |
|---|---|---|---|
| App name | n/a | App root → **Details** | Shared across versions. Once approved on Marketplace, changes are gated by Atlassian review. |
| App tagline | 130 chars | App root → **Details** | One-liner under the app name |
| App summary (Legacy) | 250 chars | App root → **Details** | Plain text shown in Marketplace search results |
| Categories + Keywords | n/a | App root → **Details** | Free-text tags + Atlassian-defined category list |
| **Highlights × 3** | **see structure below** | **App root → Versions → [version] → Highlights tab** | **Each Highlight = 3 text fields (not 1): `Title` ≤50 chars, no trailing punctuation; `Description` ≤220 chars; `Image caption` ≤220 chars. Each paired with one image at 1840×900 px. The Highlights page has a format selector at the top: Basic / Hero / Highlight / Hero & highlights. Hero & highlights is the rich format — adds a 960×600 Hero image (or video id) bound to the App tagline, plus up to 3 Highlights below. Verified 2026-05-25 against the populated v6.0.0 form.** |
| **Hero image** (Hero & highlights format only) | **960×600 px** | **App root → Versions → [version] → Highlights tab** | **Bound to the App tagline; displays as the most prominent visual on the listing page. Banner image OR video id. Separate from each Highlight's own image (1840×900).** |
| **Media gallery** | **220-char caption per image; images 1840×900 px** | **App root → Versions → [version] → Media tab** | **Independent image+caption gallery; separate UI surface from Highlights. Drag-reorderable. Use for additional screenshots that don't belong on a Highlight card (Settings, alert config, configuration screens).** |
| "More details" (long-form copy) | 1,000 chars | App root → Versions → [version] → Details tab | Awards / testimonials / extra context per the tooltip — but functionally the long-form description body, 250–1000 chars. **Markdown bold renders** (verified 2026-05-25 by inspecting the live public listing at https://marketplace.atlassian.com/apps/YOUR-LISTING-ID/flow-intelligence — `**bold**` syntax pasted in the editor renders as `<strong>` on the public listing). Other markdown (italics, links, lists) not tested. |
| Per-version pricing | n/a | App root → Versions → [version] | Inherits from app-root Pricing tab unless overridden per version. |
| Privacy & Security questionnaire | n/a | App root → Versions → [version] | DUO answers; per-version because regulatory state can shift between versions. |

**Why per-version:** Atlassian treats a Marketplace listing as a *version-controlled artifact*. App-root data is the cross-version identity; per-version data is the listing snapshot users see when they install that version. Customers stranded on an older major (because they haven't consented to the new one yet — see "Forge versioning" below) keep seeing the per-version copy that was in effect when that version was published.

**Structuring your listing copy:** a ~250-word full description does **not** map to any single field. Before submission, restructure it into 3 Highlights (220 chars each, each with a paired screenshot) + 1 "More details" block (≤1,000 chars). A ~150-char short description fits the App summary limit; a headline fits the tagline. **Editing a published version's Highlights / "More details" / screenshots is done by clicking into that version under the Versions tab.** Whether Atlassian re-reviews per-version edits to an already-approved listing is covered under "What auto-publishes to Marketplace vs. what needs review" below.

**Common confusion:** a vendor on the app-root Details tab sees tagline / summary / categories / keywords *but no "description" field* and assumes the description is missing. It's not — it's under Versions → [version], coupled with the screenshots. Two traps to avoid: `partner.atlassian.com` is the Partner Program portal, not vendor management; and Highlights are per-version, not a top-level sub-section.

**Per-version tab structure (verified 2026-05-25 from live partner console for v6.0.0):** clicking into a version under the Versions tab opens a sub-navigation with these tabs: **Details / Highlights / Media / Compatibility / Links**. Each tab edits a different per-version surface. Details holds License + Release summary + Release notes + "More details" (the long-form description). Highlights holds the format selector and the 3-Highlights authoring (each with Title / Description / Image caption + image). Media holds the independent image gallery (1840×900, 220-char captions). The Hero image (960×600, bound to the App tagline) lives on the Highlights tab when Hero & highlights format is selected, not on Media. An earlier draft of this section described "Screenshots / Media" as a single row coupled to Highlights — wrong; they're separate tabs.

**There is no separate "Features list" field.** Cross-checking the partner-console version edit page against the public Marketplace listing: every prose field in the editor surfaces somewhere on the public listing, and there's no "Features" section — so no hidden Features field exists in the editor. The four prose slots are exhaustive: tagline, App summary, 3 × Highlights, More details ("App description" — labeled "More details" with a tooltip about "awards, customer testimonials, accolades, language support" but functionally the long-form description, 250–1000 chars).

**Verification heuristic worth remembering:** when you suspect a partner-console field doesn't exist, check the public Marketplace listing for the app. If a field in the editor displays nowhere on the public listing, it's either (a) a deprecated field you should ignore, or (b) something that surfaces only post-approval and you're looking at an unfilled state. If the public listing has a section you can't find the editor field for, the editor field exists somewhere and you haven't found it yet (don't stop looking). This is faster and more reliable than re-deriving navigation from Atlassian docs.

### Forge versioning — what `forge deploy` actually creates

See [`docs/engineering/release-process.md`](release-process.md) for the change-type → version impact → CHANGELOG section → release-notes-needed-yes/no decision table. This subsection covers the empirical bump-trigger mapping and the per-version manifest evidence.

Atlassian's [Versions](https://developer.atlassian.com/platform/forge/versions/) and [Upgrading and versioning Cloud apps](https://developer.atlassian.com/platform/marketplace/upgrading-and-versioning-cloud-apps/) docs are the authoritative source for everything in this subsection. **Re-verify against those pages before acting on anything below if it has been more than ~6 months since this section was written (originally captured 2026-05-09).**

**Forge versions are `major.minor`, not three-part semver.** Apps initialize at `1.1`. There is no patch component — what you see rendered as `6.0.0` in `marketplace.atlassian.com/manage` is the Marketplace listing's display formatting (Marketplace pads to three parts); Forge itself stores `6.0`. You don't choose major-vs-minor; **Forge auto-decides based on what changed in the manifest**:

| Trigger | Bump | Source |
|---|---|---|
| Adding/modifying OAuth scopes | **Major** | Documented + observed (v4→v5 chain) |
| Enabling licensing (`licensing.enabled: true`) | **Major** | Documented + observed (v5→v6) |
| Adding a `dynamic` web trigger (`webtrigger` module — public URL surface) | **Major** | Documented |
| Adding/modifying web trigger module functions | **Major** | Documented |
| Adding/removing providers, changing provider client IDs | **Major** | Documented |
| Adding content/external permission CSP options | **Major** | Documented |
| Adding a `consumer:` manifest module (function + queue binding) | **Minor** ⚠ | **Observed 2026-05-27 prod (v6.0 → v6.1.0)**: the ADR-0033 consumer module was deployed to prod along with several other manifest additions and was classified MINOR. The prior 2026-05-26 dev observation that suggested major was wrong, or was caused by something else concurrent in that diff. |
| Adding event `trigger:` modules (`avi:forge:installed:app`, `avi:jira:*:issue`, etc. — NOT `webtrigger`) | **Minor** | **Observed 2026-05-27 prod (v6.0 → v6.1.0)**: added the `forge-installed` lifecycle trigger and `jira-issue-changed` / `jira-issue-deleted` triggers in this bundle; classified MINOR. |
| Adding `scheduledTrigger:` modules | **Minor** | **Observed 2026-05-27 prod (v6.0 → v6.1.0)**: `daily-reconcile` + `weekly-personal-data-reporting` added; classified MINOR. |
| Resolver code, Custom UI bundle, lifecycle, function timeout config | **Minor** | Documented + observed |

> *"Enabling licensing creates a new version that requires approval of the Marketplace listing, making it a major version upgrade."* — [Atlassian Versions doc](https://developer.atlassian.com/platform/forge/versions/)

**This is what forces a v5→v6-style jump.** Setting `licensing.enabled: true` to expose the Pricing tab forces a major bump. Adding granular OAuth scopes (`read:issue:jira` et al., then `report:personal-data`) also forces majors. You never pick semver — Forge does.

**Empirical correction 2026-05-27**: the only manifest changes that have empirically forced a major bump for this app are **(a) new OAuth scopes** and **(b) the `licensing.enabled` flip**. Adding consumer modules, event triggers, scheduled triggers, function entries, and timeoutSeconds config has all been MINOR in practice. The Atlassian docs' phrase "dynamic web triggers" specifically means the `webtrigger` module (public URL surface), NOT event-based triggers like `avi:forge:installed:app` — easy mis-read that produced a major-bump warning that didn't materialize. Read or change only resolver TypeScript or the Custom UI bundle and you stay minor with no admin re-consent.

**Admin consent semantics:**

> *"Forge automatically updates all installed apps to the latest minor version of their major version (without requiring admin consent)."*
>
> *"A new major version won't be applied to a site until its admin consents to the upgrade."* — [Atlassian Versions doc](https://developer.atlassian.com/platform/forge/versions/)

Practical consequence: if we ship a major bump to a site that already has us installed (e.g., example-tenant), the existing install **stays on the old major** until the Jira admin clicks accept on the upgrade prompt. Customers are not auto-broken by major bumps; they're stranded on the old version until they consent. A scope-only major thus has zero customer impact until the admin re-consents.

### Forge `jira:issuePanel` modules — Apps-button default-hidden behavior (FRGE-734)

**Observed:** 2026-06-07 during dev-tenant verification of the Jira Flow Intelligence Issue View Panel (ADR-0044).

**Behavior (observed):** Forge `jira:issuePanel` modules are hidden by default in the Jira issue view. The user must click the **Apps** (hexagon icon) button on the right side of the issue and select the panel from the dropdown to make it visible. Once added on a given issue, the panel persists for everyone who views that issue.

**Source — Atlassian's own documentation of the limitation (documented):**

- Platform issue: [FRGE-734](https://ecosystem.atlassian.net/browse/FRGE-734) (Atlassian is working on an official solution).
- Atlassian developer community admin guide (April 2025): [Enabling Jira Issue Panels by default: A guide for Jira Administrators](https://community.developer.atlassian.com/t/enabling-jira-issue-panels-by-default-a-guide-for-jira-administrators/91322). Documents a JavaScript REST-API workaround admins can run to pre-enable an issuePanel across a JQL-selected set of issues.

**What the plugin does NOT do (decision, captured 2026-06-07):** The plugin does NOT build the REST-API workaround into Jira Flow Intelligence. Maintainer decision: building it puts responsibility on the plugin author when Atlassian changes the underlying API behavior — customers would blame the plugin for breakage from an Atlassian platform change. The honest path is to document the current state, link customers to Atlassian's own published guidance, and update the docs page when Atlassian ships the FRGE-734 fix.

**Customer-facing docs (deployed):** [example.com/docs/issue-panel](https://example.com/docs/issue-panel) — the customer's setup reference for this behavior. Links to both FRGE-734 and the Atlassian community admin guide above.

### Forge environments and install lifecycle for this project

Atlassian documents three Forge environments — **development**, **staging**, **production** — per [Environments and versions](https://developer.atlassian.com/platform/forge/environments-and-versions/):

> *"We recommend using the development environment for testing your changes, staging for a stable version of your app, and production as the version of your app that's ready for use."*

The Marketplace listing publishes the **production** environment's current version. Other environments don't touch the Marketplace listing at all. Apps deployed to development get a `(DEVELOPMENT)` suffix on the install title so the install is visually distinct from production.

**Environment layout:**

| Atlassian site | Forge environment | Purpose |
|---|---|---|
| `your-site.atlassian.net` | **development** | Maintainer's test install. Every code change flows through here first. Install title shows `(DEVELOPMENT)` suffix. |
| `example-tenant.atlassian.net` | **production** | Real-ish customer pilot (a pilot tenant). Only sees Marketplace-published versions. |
| (future customers) | **production** | New Marketplace installs land here automatically. |

Until 2026-05-26 the maintainer's test install was *also* on the production environment, which structurally meant every test cycle required deploying to production and risking a Marketplace re-review + example-tenant consent prompt. That misconfiguration is fixed; the cleanup steps are codified below under "Recreating the dev install" if it ever needs to happen again.

**Lifecycle for ANY code change that touches `forge-prod/`:**

1. Build before deploy:
   ```
   cd forge-prod && npm run tsc && cd frontend && npm run build && cd ..
   ```
   Forge packages whatever is in `dist/` (resolver) and `static/main/` (Custom UI) without rebuilding. Skipping either means the deploy silently ships stale code while reporting success.

2. Deploy to development environment:
   ```
   cd forge-prod && forge deploy --environment development
   ```
   Bumps the dev-environment version. **Marketplace listing is unaffected.**

3. Apply to the dev install — minor versions auto-apply; major bumps need:
   ```
   forge install --upgrade --site your-site.atlassian.net --environment development --confirm-scopes --non-interactive
   ```

4. Test on `your-site.atlassian.net`. The install title shows `(DEVELOPMENT)` suffix; you can confirm visually.

5. When the change is verified AND any other version-affecting bundled work is also ready (per the maintainer's bundled-deploy direction):
   ```
   cd forge-prod && forge deploy --environment production
   ```
   This triggers the Marketplace re-review for major bumps and updates the public listing.

6. example-tenant' admin sees a consent prompt (only for major bumps) and opts up.

**Hard rules to prevent surprises:**

- **ALWAYS specify `--environment` on `forge deploy`, `forge install`, `forge install --upgrade`, and `forge uninstall`.** The default per `forge settings list` can drift; this ambiguity has caused a mistake at least once. Verified 2026-05-26: the current default is `development`, but rely on the explicit flag, not the default.
- **Verify install lifecycle with `forge install list` before any uninstall or upgrade.** It prints the environment + site + version for every install. A misplaced install (production env on a test site, or vice versa) is the structural cause of "every test cycle threatens customer installs."
- `--non-interactive` + `--confirm-scopes` are required when running from a non-TTY (CI, headless shells, AI assistants). Without `--non-interactive`, Forge refuses to render its "dev app on production site" warning prompt and aborts with *"Prompts can not be meaningfully rendered in non-TTY environments."*

**Demo seed fixtures on the dev tenant (`POST /api/dev/seed-demo`):**

The dev backend mounts a `POST /api/dev/seed-demo` endpoint (gated by `Settings.allow_demo_seed=True`, set only on the dev App Runner env). It loads the 250-issue Review-stage-bottleneck dataset in `app.seeds.demo` — use it for Marketplace listing screenshots, sales demos, and any "look at the live product" moment.

The seed wipes existing issues + transitions + slices for the calling tenant before re-seeding (idempotent — safe to re-run; second call produces the same row counts as the first). Trigger it from inside the Forge install on `your-site.atlassian.net` via the Settings tab's demo-seed button.

The endpoint requires tenant context (`request.state.tenant`), so it has to be called through the Forge auth middleware — the easiest path is the Settings-tab UI button or a Forge bridge call from a dev resolver, not raw curl.

**What happens when you uninstall a Forge app from an Atlassian site:**

- Forge fires `avi:forge:uninstalled:app` → the backend.
- The `tenants` row + all `issues`, `transitions`, `time_slices`, `metrics_*`, `alerts`, `alert_rules`, `wip_limits`, `sprints`, `issue_sprints`, and tenant-scoped settings are FK-CASCADE-deleted.
- Re-installing creates a fresh tenant row. ADR-0033 auto-enqueue-on-install kicks off a backfill from scratch.

For destructive uninstalls (e.g., the 2026-05-26 cleanup that re-homed the test install), the cascade is intended — the test site's state was test data. **Never run `forge uninstall` against `example-tenant.atlassian.net` or any future customer site without explicit maintainer authorization in writing.**

**Recreating the dev install (procedure used 2026-05-26):**

1. `forge install list` — verify which installs exist and their environments.
2. `forge uninstall --site <test-site> --environment production --product Jira` — removes the misplaced install. Triggers backend cascade-delete.
3. `forge deploy --environment development` — ensures the dev environment has a current version to install.
4. `forge install --site <test-site> --environment development --product Jira --confirm-scopes --non-interactive` — installs the dev-env version. Note `--non-interactive` is required for headless / AI invocation per the rule above.
5. `forge install list` — confirm: test site is now on `development`, customer sites unchanged on `production`.

### What auto-publishes to Marketplace vs. what needs review

Documented behavior of Marketplace **after** the app's first listing version is approved:

> *"We automatically detect updates to Forge apps when any changes are released to the production environment."*
>
> *"New Marketplace app versions are published to the Marketplace within a few minutes of changes being released to production via Forge CLI."*
>
> *"We want to ensure that customers get the latest version of your app with as little delay as possible — Forge apps should seem like web services, not versioned software."* — [Upgrading and versioning Cloud apps](https://developer.atlassian.com/platform/marketplace/upgrading-and-versioning-cloud-apps/)

**Steady-state rule (post-first-approval): every `forge deploy --environment production` becomes a public Marketplace version within minutes.** No "Make public" click, no per-deploy review for minor bumps. This is the opposite of pre-first-approval behavior, where listing versions stay Private until you click Make public on each.

**Partner-console listing edits — auto-publish vs. review queue.** The partner console has two distinct publishing models depending on which surface is edited:

- **Auto-publish (no queue):** App root → Versions → [version] → **Release notes / Release summary** fields. Edits go live immediately, no Atlassian re-review.
- **Atlassian review queue (typical SLA 1–3 business days):** App root → **Privacy & Security** tab (sub-processors, scope justification, data residency, certifications, and anything else that materially affects the customer-facing security disclosure). On submission the console returns a *"Responses submitted… are pending approval. We'll let you know once our team approves it"* banner with an associated support ticket. **Observed 2026-06-03** when adding Resend as a sub-processor per ADR-0040 — earlier drafts of this runbook extended the Release-notes auto-publish pattern to the Privacy & Security tab; that extension was wrong and has been corrected. **Approval-turnaround observation 2026-06-04:** the Resend sub-processor edit submitted 2026-06-03 was approved by Atlassian within ~24 hours — faster than the documented 1–3 business day window. n=1 so the documented SLA is preserved; treat this as a single fast data point, not a revised expectation.

What is **not** documented in those pages — and what should not be assumed:

- Whether **major** version bumps (scopes / licensing) trigger an automatic Atlassian re-review of the listing or auto-publish like minors. The docs say major bumps require admin re-consent; they do not say whether Atlassian re-reviews the listing. Treat any future major bump (new scope, licensing change) as if it might gate on re-review until proven otherwise. Schedule them off the critical path. **Note 2026-05-27:** the 6.0 → 6.1.0 deploy was MINOR and auto-published in seconds with the "Released by" field showing "Marketplace Hub [Atlassian]" — i.e., automated, no human reviewer. This confirms minor auto-publish but tells us nothing about major-bump behavior.
- Whether per-version **listing metadata** (Highlights, screenshots, "More details", per-version pricing, Privacy & Security answers) auto-carries-forward to a new version. **Observed 2026-05-09:** clean inheritance from v5.0.0 → v6.0.0 in the vendor console; new version pre-populated with the prior version's per-version data, editable in place. Atlassian does not document this behavior, so don't rely on it for future majors without spot-checking that every per-version sub-section (Highlights, Media, "More details", Pricing, Privacy & Security) populated correctly on the new version. **Confirmed 2026-05-25:** the Highlights / screenshots / "More details" *fields themselves* live per-version (under Versions → [version]), so "carries forward" specifically means Atlassian copies the previous version's values into the new version's edit form, not that they share storage.

### Deploying to Forge production while a listing version is in review — UNDOCUMENTED

Confirmed gap as of 2026-05-09: none of [listing-forge-apps](https://developer.atlassian.com/platform/marketplace/listing-forge-apps/), [upgrading-and-versioning-cloud-apps](https://developer.atlassian.com/platform/marketplace/upgrading-and-versioning-cloud-apps/), [forge versions](https://developer.atlassian.com/platform/forge/versions/), or the environments-and-versions page describes what happens when:

- A listing version (e.g., v6.0.0) is in Atlassian's review queue, **and**
- The developer runs `forge deploy --environment production` (creating a new Forge runtime version, e.g., a v6.1 minor)

Specifically not addressed:
- Whether the in-review version is locked against being overwritten by a new deploy.
- Whether the new deploy creates a separate Marketplace listing version that needs its own submission.
- Whether the new deploy is queued, ignored, or replaces the in-review submission.
- Whether the listing approval, when it lands, applies to the version that was submitted or to "the latest production deploy at approval time."

**Operating rule: do not run `forge deploy --environment production` while a listing version is in Atlassian's review queue.** The cost of waiting (5–15 business days per the listing-forge-apps page's *"we aim to provide a decision within one week"*) is bounded; the cost of getting it wrong (review reset, listing rejection, customer-visible breakage on approval) is not.

**Backend / infra deploys via `git push` are also unsafe during review** — see "Push freeze: GitHub Actions deploys during listing review" below. An earlier draft of this runbook claimed they were safe because "Marketplace doesn't see them." That conflation was wrong: the Marketplace registry doesn't render the backend, but the human reviewers exercising the app *do* — the compliance URLs (`/privacy`, `/terms`, `/support`, `/sla`, `/docs`, `/security`) are filed directly in the listing form, and the Forge app's runtime calls `api.example.com` during their testing.

**If a Forge-side fix becomes urgent during review**, email `developer-experience@atlassian.com` or `ecosystem@atlassian.com` first and ask explicitly. Do not guess from UI behavior.

### Push freeze: GitHub Actions deploys during listing review

**Operating rule: during any active Atlassian Marketplace listing review, no pushes to any branch that triggers `deploy.yml`. Forge deploys also stay frozen (per the prior subsection); the two freezes are separate but both apply.**

Why this is broader than it sounds. `.github/workflows/deploy.yml` triggers on `push` to `main`, `develop`, or `feature/**`. **There is no `paths` or `paths-ignore` filter.** Every push to those branches runs the full pipeline — including docs-only commits. Specifically (see `.github/workflows/deploy.yml`):

| Stage | Lines | What it does on every push |
|---|---|---|
| ECR stack | 85–91 | `cdk deploy EcrStack` (usually no-op once repos exist) |
| Docker build + push | 101–111 | New backend image tagged `:${github.sha}` and `:latest` |
| Compute stack deploy | 126–136 | `cdk deploy` with `-c image_tag=${github.sha}` — context changes every commit, so CFN sees a diff and **App Runner redeploys** |
| Smoke test | 177–193 | Polls `${BackendUrl}/healthz` |

**Why this matters during review:** Atlassian reviewers exercise the reviewer-visible surfaces of this pipeline:

1. The compliance URLs filed in the listing form (Privacy, Terms, Support, SLA, Documentation, Security pages). They click these directly from the partner-console submission.
2. The Forge app's backend at `api.example.com` (App Runner). The Forge app calls it during their functional testing.

A regression on either surface during the review window can cause a rejection. Cost asymmetry is decisive: **bounded delay** while waiting (5–15 business days per track) vs. **unbounded re-submission cost** if a regression-induced rejection forces a second review.

**Even pure-docs commits aren't zero-risk to push.** A docs-only push still rebuilds the Docker image, redeploys App Runner (container roll, brief interruption window), and re-runs the smoke test (which can transiently fail and trip alarms). Probability of customer or reviewer harm is low; benefit during review is zero.

**Mechanics of the freeze:**

1. Commit locally on `main` as normal — local commits don't push anywhere.
2. **Do not run `git push origin main`** while review is in flight.
3. **Do not push to `develop`** or any branch matching `feature/**` either — same pipeline.
4. **Off-machine backup escape valve:** `git push origin main:freeze-pre-approval` (or any branch name *not* matching `main` / `develop` / `feature/**`). The deploy workflow ignores it, so no pipeline runs, but the commits exist on `origin` for backup.
5. The Forge-CLI freeze in the prior subsection still applies independently. **Do not run `forge deploy --environment production`** during review either.

**Lifting the freeze (after Atlassian approves the listing):**

1. Both review-track approvals land (Privacy & Security + Listing).
2. Push `main`. Watch the GitHub Actions run end-to-end.
3. Within ~10 minutes of pipeline completion: confirm each filed compliance URL renders; `curl https://api.example.com/healthz` returns 200.
4. If a backlog of held commits has accumulated: push them as separate atomic commits (one push per logical change) rather than one bulk push — keeps the deploy log readable and makes any post-deploy regression easy to bisect.

**Per-push checklist for the post-approval steady state** (this is the steady-state hygiene, not the freeze policy — applies after approval):

- Touches only `docs/`, `CLAUDE.md`, ADRs, plans? → Low-risk push.
- Touches `backend/`? → After deploy, verify smoke test passes; spot-check a FIT-protected endpoint via `forge tunnel` against dev before pushing.
- Touches `forge-prod/manifest.yml`? → Stop. Run the major-vs-minor analysis from the "Forge versioning" subsection above. If major (scopes, licensing, dynamic triggers): schedule it deliberately, communicate the admin re-consent prompt to live tenants, and treat it as a release.
- Touches `.github/workflows/`? → Push during a quiet window; watch the resulting deploy run carefully.

**Historical record of freezes:**

- **2026-05-09 → 2026-05-20: Jira Flow Intelligence v6.0.0 first Marketplace listing review.** Both tracks (Privacy & Security + Listing) approved by the assigned Atlassian reviewer; published by `mpac-pluginchecker-bot`. ~11-day review window. Notable incidents during the freeze:
  - **2026-05-19: Atlassian rotated the FIT signing key.** Backend started returning 401 to all FITs with `kid not in cached JWKS at /app/forge_jwks.json`. Atlassian reviewer hit the error during functional testing and reported it. Freeze lifted for a one-shot redeploy per the procedure in "Forge JWKS rotated — backend now rejects all FITs" above; freeze auto-restored after deploy completed and `/healthz` returned 200. Approval landed the next day (2026-05-20). Lesson: this freeze policy worked, but the underlying JWKS-staleness vulnerability is still latent — the proactive-refresh TODO captured in `docs/security-review/security-self-assessment.md` is the durable fix.

### Dead `JiraClient` code in the backend (legacy from pre-Forge)

`backend/app/services/jira_client.py` implements an HTTP Basic Auth client (`JIRA_EMAIL` + `JIRA_API_TOKEN`) that calls `https://<site>.atlassian.net/rest/api/3/search` and `…/changelog` directly. It is referenced by `backend/app/services/ingestion_service.py:sync_from_jira` and surfaced via `backend/app/routers/sync.py`.

**This code is dead in production.** `JIRA_EMAIL` and `JIRA_API_TOKEN` are **not** wired into `infra/stacks/compute_stack.py` — neither environment variable is ever set in the App Runner task. Any call to the `JiraClient` constructor in prod hits the `JiraAuthError("Jira credentials missing — set JIRA_EMAIL and JIRA_API_TOKEN.")` raise immediately. There is no path in the current resolver wiring that exercises `sync_from_jira` in the prod data flow: all Jira ingestion goes through Forge triggers (`issueWebhookResolver`, `reconcileResolver`) which POST to `/api/forge/sync/ingest`, bypassing `JiraClient` entirely.

**Why this is a footgun nonetheless:**

- If someone copies the staging or dev config into prod and accidentally sets those env vars, the dead path becomes live. The Forge architecture's whole point (per ADR-0019) is that the backend never talks to Jira directly — accidentally re-enabling Basic Auth would violate that invariant.
- A reviewer (Atlassian or third-party) grepping `backend/` for `Basic` / `api_token` / `JIRA_API_TOKEN` lands on this file and reasonably asks "is your app collecting customer Atlassian credentials?" The answer is no, but the existence of the code forces an explanation.
- It accrues maintenance cost (CI runs lint + types over it) for no production value.

**Post-freeze cleanup:** delete `backend/app/services/jira_client.py`, `backend/app/services/ingestion_service.py:sync_from_jira` (and any tests touching it), and remove the unused config keys `jira_base_url` / `jira_email` / `jira_api_token` from `backend/app/core/config.py`. The `/api/forge/sync/ingest` path remains — that's the live production path. Captured as a TODO in `docs/security-review/security-self-assessment.md` "Post-approval bundled deploy".

This was not deleted during the freeze because it is backend code: deleting it changes what would deploy on the next push, and the freeze policy is to not change the deployable surface at all during a listing review.

### Updating the listing after first approval

Once v6.0.0 (or the first-listed version, more generally) is approved:

- **Routine deploys** (resolver code, Custom UI changes, scheduled trigger logic, backend CDK): just `forge deploy --environment production` and/or `git push` per environment. Marketplace auto-publishes the Forge minor version within minutes per the documented behavior above. No partner-console action needed.
- **Manifest changes that force a major bump** (new scopes, licensing flips, and the other "Major"-tagged rows in the table in the "Forge versioning" subsection above — but NOT consumer modules, event triggers, or scheduled triggers, per the 2026-05-27 empirical correction there): treat as a formal release. Stop deploys, communicate the scope of the change, deploy during a low-traffic window, and verify the partner console shows the expected major version with all listing metadata still populated. Watch any live tenants for the admin re-consent prompt; if any tenant doesn't re-consent, they remain on the prior major.
- **Listing copy / screenshots / pricing changes** (no code change): edit via `partner.atlassian.com` → vendor → app → **Edit listing**. Some edits unlock immediately (copy fixes); others trigger the Atlassian review queue. Specifically — **Privacy & Security questionnaire edits go through the review queue (typical SLA 1–3 business days; observed 2026-06-03)**; pricing structure changes also re-run review. The listing partner console surfaces the pending-approval banner when you save. See "What auto-publishes to Marketplace vs. what needs review" above for the full auto-publish-vs-queue map.

### Pre-public-customer-onboarding checklist

Some things are safe today (with only example-tenant + the maintainer's dev install) but must be tightened **before publicly inviting new customer installs**. Track each here; flip during a quiet deploy window.

- [x] **`infra/stacks/_config.py` → `EnvConfig.allow_demo_seed`**: ~~currently True on prod~~ Flipped to **False on 2026-05-27** (commit follows this edit). This unmounts `/api/dev/seed-demo` on the prod backend (closes the backend gate). The UI gate in `SettingsTab.tsx` already hides the panel for production-env Forge installs, so defense-in-depth is now both layers. Re-flip to True for short windows when a re-seed is needed.
- [ ] **Multi-AZ RDS** (`EnvConfig.rds_multi_az`): currently False on prod. **DEFERRED 2026-05-27 — maintainer direction**, no longer gated on "publicly inviting new customers." Revisit only on explicit maintainer request. Cost note: ~$30/mo additional.
- [ ] **Pen-test report** (deferred): **DEFERRED 2026-05-27 — maintainer direction**, no longer gated on "publicly inviting new customers." Revisit only on explicit maintainer request (e.g., when an enterprise customer requires it).
- [ ] **Cloud Fortified badge** (deferred): nice-to-have; revisit later.

## Operational alerting

Per [ADR-0030](adr/0030-operational-alerting.md). The CloudWatch alarms in `infra/stacks/observability_stack.py` are wired to an SNS topic (`flow-intelligence-{env}-alerts`) with an email subscription to `alerts@example.com`. This section documents what pages, when, and how to verify it's working.

### What pages

| Alarm | Condition | Time to page (worst case) | What it means |
|---|---|---|---|
| `flow-intelligence-prod-billing` | EstimatedCharges > $200 (6h period) | ~6h after threshold crossed | Cost overrun. Investigate before continuing usage. |
| `flow-intelligence-prod-backend-5xx` | App Runner 5xx > 10 in each of 2× 5min windows | ~10 min | Backend returning server errors. Check logs + dashboard. |
| `flow-intelligence-prod-backend-latency` | App Runner p95 > 5s for 3× 5min windows | ~15 min | Backend slow. Usually a slow query or external dep. |
| `flow-intelligence-prod-backend-4xx` | App Runner 4xx > 15 in each of 2× 5min windows | ~10 min | Sustained client-error elevation — auth break, validation regression, or misbehaving client. |
| `flow-intelligence-prod-backend-forge-auth` | Any `Forge FIT validation failed` log line in 5min | ~5 min | **The JWKS-rotation 401 class** (see "Forge JWKS rotated" below). With ADR-0029's in-process refresh, this should be zero in steady state; *any* hit means the refresh path itself failed or a non-rotation auth issue. |
| `flow-intelligence-prod-backend-healthz` | Route 53 HTTPS check of `api.example.com/healthz` returns non-200 / body missing `"status":"ok"` for 2× 1min | ~3 min | Backend is *down* (not just emitting bad responses). The only external-perspective probe. |

### First-time setup (per environment)

CDK creates the SNS topic and wires alarm actions to it, but does **not** create the email subscription. Subscriptions must be created via CLI with `AuthenticateOnUnsubscribe=true` to defend against inbox link-scanner auto-unsubscribe (see "Known interaction" below for the incident; ADR-0030 for full rationale).

After `cdk deploy ObservabilityStack`:

```bash
# <YOUR_AWS_ACCOUNT_ID> — set to your 12-digit AWS account id
aws sns subscribe --region us-east-1 \
  --topic-arn arn:aws:sns:us-east-1:<YOUR_AWS_ACCOUNT_ID>:flow-intelligence-prod-alerts \
  --protocol email \
  --notification-endpoint alerts@example.com \
  --attributes AuthenticateOnUnsubscribe=true
```

Then:

1. Check `alerts@example.com` inbox for a message from `no-reply@sns.amazonaws.com` titled "AWS Notification - Subscription Confirmation".
2. Click **Confirm subscription** in the email body.
3. Verify the subscription persisted past the auto-unsubscribe window: wait 2 minutes, then `aws sns list-subscriptions-by-topic --topic-arn <arn>` should show a real subscription ARN (not `Deleted`).
4. (Optional) Verify alarm delivery: force any alarm to ALARM with `aws cloudwatch set-alarm-state --alarm-name <name> --state-value ALARM --state-reason "delivery test"`. **Do NOT click any link in the alert email** — the unsubscribe URL is now auth-protected but the inbox scanner pre-fetch could still hit unrelated links. Reset to OK afterward.

If you forget the `--attributes AuthenticateOnUnsubscribe=true` parameter, the subscription will be silently auto-unsubscribed by Google Workspace's mail safety scanner within seconds of confirming. There is no way to add the attribute after subscribing — you must `unsubscribe` and re-`subscribe` with the parameter.

### Removing a recipient

`sns:Unsubscribe` is denied to anonymous callers by the topic policy (see ADR-0030 "Authenticated-unsubscribe enforcement" for why — Gmail's link scanner was auto-unsubscribing the subscription). Self-service unsubscribe via the email-footer link does NOT work and is intentional.

To remove a recipient, list and then unsubscribe via CLI from an authenticated session in this account:

```bash
# <YOUR_AWS_ACCOUNT_ID> — set to your 12-digit AWS account id
aws sns list-subscriptions-by-topic --region us-east-1 \
  --topic-arn arn:aws:sns:us-east-1:<YOUR_AWS_ACCOUNT_ID>:flow-intelligence-prod-alerts
aws sns unsubscribe --region us-east-1 --subscription-arn <arn-from-above>
```

### Known interaction: link-scanner auto-unsubscribe (resolved)

**2026-05-22.** Twice in a row, an email subscription to `alerts@example.com` (Google Workspace) was auto-deactivated within seconds of being confirmed. SES suppression list was empty; CloudTrail showed no `Unsubscribe` API call; SNS delivery metric showed `NumberOfNotificationsDelivered=1, Failed=0` so SNS believed it was delivering. Root cause: Workspace's inbound mail security follows every URL in every message for safety scanning. SNS embeds an unauthenticated one-click unsubscribe URL in every notification email and in the confirmation flow's response page. The scanner's GET against that URL is indistinguishable from a human click — instant unsubscribe, no user action.

Other mail security stacks known to do this: Mimecast, Microsoft Defender for Office 365, Proofpoint, Cisco Secure Email, Barracuda Email Gateway.

**Investigation false start.** First fix attempted was a topic policy denying `sns:Unsubscribe` to anonymous principals, per a literal reading of [AWS KB "Prevent users from unsubscribing from an SNS topic"](https://repost.aws/knowledge-center/prevent-unsubscribe-all-sns-topic). The deploy failed with `Invalid parameter: Policy statement action out of service scope` — `sns:Unsubscribe` is a subscription-scope action and cannot appear in an SNS topic-resource policy. The KB article's example is misleading; the topic-policy approach does not work.

**Actual fix.** The `AuthenticateOnUnsubscribe=true` per-subscription attribute, settable only as a parameter to the `Subscribe` API call (not via `SetSubscriptionAttributes`, not via CloudFormation). With it set, the unsubscribe URL in notification emails requires a SigV4-signed request; the inbox scanner's anonymous GET returns AccessDenied and the subscription stays alive. Because CloudFormation doesn't expose this attribute, the subscription is now managed out-of-band via CLI — CDK manages topic + alarm wiring only. The CDK test `test_observability_creates_alert_topic` asserts the stack creates zero subscriptions, so a future contributor can't accidentally re-introduce a CDK-managed subscription that would silently get scanner-unsubscribed. See ADR-0030 "Authenticated-unsubscribe enforcement" for the full reasoning.

### Operational reaction patterns

- **`backend-forge-auth` fires alone, no `backend-healthz`:** auth path issue. Check the application logs for the failing kid. If the kid pattern matches `forge/invocation-token/wf-*`, it's almost certainly an Atlassian rotation that the in-process refresh didn't recover; jump to "Forge JWKS rotated" below.
- **`backend-healthz` fires:** App Runner service is down. Check the service in the App Runner console (`flow-intelligence-prod`). If the service is RUNNING but failing /healthz, check the recent deploy or container logs for crash loops. The manual recovery is usually a rollback or a fresh deploy.
- **`backend-4xx` + `backend-forge-auth` both firing:** a customer-facing auth break, exactly the 2026-05-19 / 2026-05-22 pattern. Treat as P0.
- **`backend-5xx` alone:** check the trailing log lines for the most recent ERROR-level events. Most 5xx come from unhandled exceptions in the routers or from the database being unreachable.
- **`backend-latency` alone, no errors:** slowness without errors usually means a slow upstream (Atlassian rate limiting, Anthropic API). Check the operator dashboard's request volume; investigate the slowest endpoint by inspecting structured logs.
- **`billing` alarm:** Open the AWS Billing console and look at the cost-by-service breakdown for the current month. Most likely culprits historically: NAT Gateway (if `nat_gateway` got flipped on for dev), App Runner over-provisioning, RDS storage growth.

### Adding more channels later

SMS, Slack, or PagerDuty additions all fan out from the same SNS topic — no alarm changes required. The CDK pattern is:

```python
# SMS
alert_topic.add_subscription(sns_subs.SmsSubscription("+15551234567"))

# Slack (via webhook + Lambda transformer or AWS Chatbot)
# Lambda fanout: subscribe a function to the topic; function POSTs to a Slack webhook URL.
# AWS Chatbot: connect Slack workspace, subscribe channel to the topic, no code.
```

Adding a second email recipient is one extra `add_subscription` line. The bootstrap-confirm step applies per new email subscriber.

## Deferred work (pre-production gates)

These are documented gaps. None blocks local dev or CI; all block a public production deployment.

| Gap | Impact | Tracker |
|-----|--------|---------|
| No incremental sync (always full re-sync) | Wasteful at scale; ~OK at <10k issues | ADR + design doc needed |
| No live Jira test in CI | Field-mapping regressions only caught in staging | Could mock with VCR cassettes |
| `[postgres]` extra unverified in CI | A `psycopg`-specific bug would slip through | Add Postgres matrix when needed |
| AI live call path not exercised | Template fallback masks API regressions | Add mocked-SDK contract test |
| No structured logs / tracing | Hard to debug live incidents | `structlog` + OpenTelemetry — separate ADR |
| No background scheduler | Sync is manual via `POST /api/sync` | APScheduler or worker — separate ADR |
| Postgres RLS not yet enabled | Defense-in-depth gap; service-layer tenant_id filtering is current guard (ADR-0011) | Phase 2 hardening before public Marketplace |
| Orphans on missed `avi:jira:deleted:issue` events | Issue rows persist in the DB after delete in Jira; charts show stale tickets | Resync-driven sweep planned (task #71); revisit when a customer reports it or observability catches it |

## Sync semantics — idempotency + delete handling

Three sync paths share one ingest pipeline. All are safe to re-run:

- **Refresh now / Force full** (manual button → `syncJira` resolver): incremental or full-window.
- **Webhook + scheduled reconcile** (ADR-0024): per-issue events + daily catch-up.
- **Backfill loop**: one-time / on-demand pull of all history with no time floor.

**Idempotency, by table**:

| Table | Strategy | Citation |
|---|---|---|
| `issues` | Composite PK `(tenant_id, id)`; `session.get` → update-in-place if present, else insert. No duplicates structurally possible. | `backend/app/services/ingestion_service.py:87-90` |
| `transitions` | `replace_transitions` does `DELETE WHERE tenant_id=X AND issue_id=Y` then bulk insert. Docstring: *"Idempotent replacement of all transitions for an issue."* | `backend/app/services/transition_service.py:71-97` |
| `time_slices` | `replace_time_slices` follows the same DELETE-then-INSERT pattern. *"Atomic replacement of slices for an issue (idempotent recompute)."* | `backend/app/services/slicing_service.py` |
| `sprints` | `upsert_sprint` updates by composite PK `(tenant_id, id)`. | `backend/app/services/sprint_service.py:upsert_sprint` |
| `issue_sprints` | `set_issue_sprints` replaces membership: drops absent entries, inserts new ones. | `backend/app/services/sprint_service.py:set_issue_sprints` |
| `alerts` | Unique `(tenant_id, rule_id, issue_id, status, key)` + explicit existence check before insert (handles SQLite NULL-distinct semantics). Per [ADR-0041](adr/0041-state-based-alert-refire-daily-bucket.md) the `key` for state-based ticket-level rules includes the UTC date so a perpetually-breaching condition re-fires once per UTC day. | `backend/app/services/alert_service.py:_persist` |

**`skip_if_stale=True` semantics**: when set on `/api/forge/sync/ingest`, payloads where `existing.updated_at >= payload.fields.updated` are skipped entirely — no DB writes, no recompute. Used by the webhook resolver and the backfill loop. Bulk Sync paths leave it `False` so Force-full can re-process even apparently-unchanged issues (necessary after a schema change adding new fields). Implementation: `backend/app/services/ingestion_service.py:248-253`.

**Delete handling**: only `avi:jira:deleted:issue` triggers row removal (`forge-prod/src/resolvers/webhooks.ts → issueDeletedResolver` → `DELETE /api/forge/sync/issues/{id}` → backend cascades). A re-sync or backfill **does not** detect deletions — Jira's search returns only existing issues, and orphaned rows in the DB persist. This is a known gap; resync-driven cleanup is tracked as task #71.

**What this means for "is it safe to click X twice?"**:
- Issues that haven't changed → skipped server-side (cheap, no churn).
- Issues that changed → in-place update + DELETE-and-INSERT for transitions/slices.
- New issues → pulled in fresh.
- Deleted issues (rare miss case) → not removed by re-sync; need the webhook or task #71's sweep.

## Common operations

### "The dashboard says no bottleneck and I expected one"

1. Confirm the relevant issues exist: `curl http://localhost:8000/api/issues | jq '.[].key'`
2. Inspect a single issue's slices: `curl http://localhost:8000/api/issues/ABC-123 | jq .time_slices`
3. Inspect the raw insight calculation: `curl 'http://localhost:8000/api/insights?days=7&explain=false' | jq`
4. Compare current vs. previous window: `curl 'http://localhost:8000/api/metrics?days=7' | jq`
5. If a status has no `previous` data (new status, or fresh project), it's deliberately skipped — see ADR-0007.

### "Alerts won't fire"

1. List rules: `curl http://localhost:8000/api/alerts/rules | jq`
2. Check thresholds against the actual slice durations.
3. Force evaluation: `curl -X POST 'http://localhost:8000/api/alerts/evaluate?days=7' | jq`
4. Remember alert idempotency ([ADR-0041](adr/0041-state-based-alert-refire-daily-bucket.md), supersedes ADR-0008): a rule won't re-fire for the same `(rule_id, issue_id, status, key)` tuple. For state-based ticket-level rules (`cycle_time`, `status_duration`, `no_activity`) the `key` includes the UTC date — re-evaluating the same day produces no new alert; the next UTC day produces a fresh alert if the breach is still present.

### Customer alert re-fire cadence (ADR-0041)

State-based ticket-level rules fire **once per UTC day per breaching ticket**, not once per breach event. Operators reading the `alerts` table for a perpetually-stuck ticket should expect roughly one row per day that ticket has been past threshold:

- A ticket stuck for 14 days under a `cycle_time > 7d` rule will produce **~7 alert rows** (one per UTC day it spent in the breaching state past day 7), not one ever and not one per evaluation.
- `wip_breach` re-fires on the rule's own evaluation cadence (per the customer's `breach_minutes` setting via ADR-0037 cadence-tiering), not the daily bucket — by design.
- `trend` re-fires on its own hourly bucket — by design.

If a customer reports "I never see the cycle-time alert again after the first day," check that the deployed version is after the ADR-0041 supersession (commits on or after 2026-06-05); pre-ADR-0041 backends will exhibit the fire-once-then-silent pattern that ADR-0008 originally locked in.

### External-blocking status set (ADR-0042)

Per-tenant `external_blocking_statuses` set excludes statuses from the bottleneck card's **attribution** step while preserving their slice data and chart visibility. The bottleneck card answers *"where is the team's controllable bottleneck?"* — time the team can't act on (waiting on customer, vendor, external review) does not drive attribution.

- **Storage:** `tenants.external_blocking_statuses` (JSON column, nullable). NULL = inherit `Settings.external_blocking_statuses` (ships as `[]`). Operator can override via `PUT /api/tenant/settings` or the Settings UI's *"External-blocking statuses"* picker.
- **Scoring effect:** `insight_service.detect_bottleneck` skips statuses in the set (case-folded, same mechanism as the `terminal_statuses` filter at the same callsite).
- **Slice data unaffected:** `time_slices` rows for external-blocking statuses are still recorded with full duration. CFD and time-by-status charts still display them. Per-issue history still surfaces them.
- **Trends unaffected by design:** `compute_trends` continues to surface external-blocking statuses — the trend signal ("waiting-on-customer time grew +60% vs prior window") remains operationally useful even though the bottleneck card excludes the status from attribution.
- **Empty list is a valid explicit value:** unlike `active_statuses` / `done_statuses` / `terminal_statuses` (where an empty list would be a footgun), `external_blocking_statuses = []` is the safe, intentional default. The Settings UI's `StatusListRow` honors `allowEmpty` so removing the last chip saves `[]` (not `null`) for this field.

If a customer reports *"my bottleneck card still names a Blocked status"*, check `ctx.external_blocking_statuses` — likely empty (default). Add their Blocked status via Settings; the next `/api/insights` call surfaces a different (non-paused) attribution. **No recompute needed** — this is a query-time filter, not a stored attribute.

### "Tests pass locally but fail in CI"

- Always check: are you on the same Python version as CI? `python --version` vs. the matrix.
- Watch for non-deterministic time-of-day in tests — pass an explicit `now=` parameter.
- SQLite `:memory:` requires `StaticPool` for shared-connection tests; see `test_routers.py`.

### "How do I hit a protected endpoint with `curl` from my laptop?"

Every non-skip-listed endpoint requires a valid Forge Invocation Token (ADR-0019). FITs are RS256-signed by Atlassian and short-lived; they can't be minted locally. Two practical paths:

- **Run `forge tunnel` from the `forge/` directory** and exercise the endpoints from inside Jira. The Forge runtime forwards the FIT for you.
- **Skip auth in a local dev run**: leave `FORGE_APP_ID` unset in your shell. The middleware logs a warning and runs without auth — fine for `curl` testing the engine, never deploy that way.

### Verifying that a route is *registered* on the deployed backend

**Use `/openapi.json`, not curl status codes.** Established 2026-06-07 after a diagnostic mistake: an unauthenticated `curl` returns 401 for *every* non-skip-listed path — registered OR not — because [`ForgeAuthMiddleware`](../../backend/app/forge/middleware.py) gates the request *before* FastAPI route resolution. So 401 tells you the path matches the auth gate; it tells you nothing about whether the route past auth actually exists.

```bash
# Decisive route-registration check. /openapi.json is in SKIP_PATH_PREFIXES,
# bypasses auth, and reflects the actual registered routes on the deployed
# image.
curl -sS https://api.example.com/openapi.json \
  | python3 -c "import json, sys; d = json.load(sys.stdin); print('\n'.join(sorted(d['paths'])))"

# Or, filtered to a specific group you're investigating:
curl -sS https://api.example.com/openapi.json \
  | python3 -c "import json, sys; d = json.load(sys.stdin); print('\n'.join(sorted(p for p in d['paths'] if 'schedule' in p)))"
```

**When this matters:** any time a deploy-gap question comes up ("the route was merged days ago — is it actually live?"). The 2026-06-06 TMT-gap workstream's dev-tenant 404 was caused by exactly this gap and concealed for ~24 hours by curl-status-codes that said 401. `/openapi.json` would have surfaced the absence of `/api/forge/schedule/*` paths in one query.

**Sanity table for the response shapes:**

| You see | What it actually means |
|---|---|
| Path in `/openapi.json` AND endpoint returns 401 to unauth `curl` | Route registered, auth working. |
| Path NOT in `/openapi.json` AND endpoint returns 401 to unauth `curl` | Route is NOT registered. Middleware blanket-401s any non-skip path. |
| Path in `/openapi.json` AND authenticated request returns 404 | Route registered; the *handler* raised `HTTPException(404, ...)`. Read the handler code, not just the wire response. |
| Path NOT in `/openapi.json` AND authenticated request returns 404 | Route is NOT registered. Same as the second row from a different observer. |

The bottom two rows produce indistinguishable wire responses (FastAPI's default 404 body `{"detail":"Not Found"}` is the same for "no route matched" and `raise HTTPException(404)`); the only way to distinguish them is to check `/openapi.json` for the path AND read the handler source.

### Stop the dev NAT Gateway meter

The backend uses a VPC connector to reach RDS. Without a NAT Gateway, App Runner ENIs in the VPC have no path to the public internet — Anthropic API calls (used for AI explanations on the bottleneck card) time out and the engine falls back to a templated sentence. Prod runs with NAT permanently on; dev runs with NAT on in CDK so toggles work in CDK, but the live NAT can be deleted to stop the meter when not actively demoing AI.

CDK's nat_gateway=True for dev is sticky on purpose: flipping it to False mid-stack triggers a cross-stack export drop (private subnet IDs that Compute imports), which CFN can't complete in a single deploy. Resolving that properly is Phase-3-grade work; the manual-delete escape hatch costs less and works today.

**To stop the meter** (after AI demos):

```
NAT_ID=$(aws ec2 describe-nat-gateways --filter Name=state,Values=available --query 'NatGateways[0].NatGatewayId' --output text)
aws ec2 delete-nat-gateway --nat-gateway-id "$NAT_ID"
EIP_ID=$(aws ec2 describe-addresses --query 'Addresses[?AssociationId==null].AllocationId | [0]' --output text)
aws ec2 release-address --allocation-id "$EIP_ID"
```

Backend continues to work — RDS access is via the VPC connector + SG, unaffected. AI calls revert to the templated sentence (same fallback as before NAT was ever introduced). RDS data is untouched.

**To re-enable AI** (next demo):

CFN doesn't reconcile drift on a no-op deploy — it only acts on template diffs. To force NAT recreation, push a deploy that actually touches NetworkStack (any code change in `infra/stacks/network_stack.py` will do — even a comment). CDK then sees the NAT is missing in CFN state vs. expected, and creates a new one (with new EIP, new ID; route tables on the private subnets get repointed). Roughly 5 minutes from push to AI working again.

**Cost while on:** $0.045/hour (~$32/mo) + ~$0.045/GB processed (negligible at typical scale). Billing alarm on dev is $50; expect it to fire if NAT stays on more than ~12 days in a month.

### "Forge JWKS rotated — backend now rejects all FITs"

The container bakes Atlassian's JWKS at build time. When Atlassian rotates a signing key, FITs are signed with a `kid` the baked JWKS doesn't contain.

**As of ADR-0029 (2026-05-22) this is self-healing.** `_StaticFileJwkResolver` detects the cache miss, takes its lock, performs a single rate-limited live fetch from `https://forge.cdn.prod.atlassian-dev.net/.well-known/jwks.json` (timeout 5s, max 1 fetch per 60s per process), merges any new keys into the in-memory cache, and retries the lookup. Prod's NAT Gateway (`infra/stacks/_config.py` `nat_gateway=True`) gives App Runner the egress path. End-user impact of a rotation: a single request takes up to 5s of additional latency; subsequent requests are cache-hits. No 401, no operator action, no deploy.

Operational signals to watch for in CloudWatch:
- `JWKS live refresh from https://forge.cdn.prod.atlassian-dev.net/.well-known/jwks.json added N new key(s)` — successful self-heal. Expected occasionally; nothing to do.
- `JWKS live refresh from <url> failed: <reason>` — Atlassian unreachable or the JWKS endpoint returned malformed JSON. Investigate (network? Atlassian outage?); the resolver will retry on the next miss after the rate-limit window expires.
- 401 with `kid <id> not in cached JWKS at /app/forge_jwks.json; live refresh from <url> did not yield kid` — the live fetch succeeded but the kid still isn't present. Either the token is genuinely bad (spoofed, malformed, from a different Forge environment) or Atlassian's JWKS endpoint is serving stale data. Capture the exact kid before doing anything else.

**Manual recovery is the fallback if the self-heal path is broken** (e.g., NAT route is down, Atlassian DNS resolution is failing inside the VPC, or a regression in `fit_auth.py` has broken the refresh path):

```bash
git commit --allow-empty -m "chore: redeploy to refresh Forge JWKS"
git push
```

The Dockerfile re-fetches the JWKS during the runtime stage, so a fresh build picks up the new key. Full pipeline ~12–13 minutes.

**Confirmed in production twice before the durable fix landed:**

- **2026-05-19** — Atlassian rotated during the v6.0.0 listing review; reviewer hit the 401, manual redeploy recovered.
- **2026-05-22** — Atlassian rotated two days after Marketplace approval; a paying-customer dashboard hit the 401. Manual redeploy recovered, and the durable fix shipped in the same session per ADR-0029.

**Freeze interaction.** Manual recovery requires a push to `main`, which a push freeze would forbid. The freeze rule's wording covers this case: the freeze exists to prevent introducing *new* regressions during a review. When the existing deploy already has a regression that's blocking the reviewer, the freeze must yield — but only by explicit user instruction, and only for the one-shot push. Restore the freeze automatically after the deploy. The self-heal path means the freeze should never need to yield for this reason going forward.

### "Capture the real Forge trigger payload shape"

The resolver logged `issueWebhookResolver: no issue.id in context` without a clear signal of which key path Forge actually delivers the issue id under. The resolver now extracts from all known paths (`context.issue.id`, `payload.issue.id`, top-level `issue.id`, `issueId` variants) and, when none match, logs the full args JSON at `console.warn` with prefix `no issue.id in any known path; args=...`.

To collect a real sample once one fires:

```sh
aws logs tail /aws/lambda/<forge-function-name> --since 24h --region us-east-1 \
  | grep "no issue.id in any known path"
```

If the captured `args=` JSON shows a path none of the existing extractors cover, add it to `extractIssueId` in `forge-prod/src/resolvers/webhooks.ts` and bump the resolver. If the warning never appears in 7 days of normal traffic, the multi-path extractor is sufficient and the work is done — close as resolved.

For a faster signal, run `cd forge-prod && forge tunnel` and trigger a Jira issue update against the tunneled install; the same `console.warn` will show up in the tunnel's stderr.

### "Stale Forge tenant row blocks a fresh install"

Forge fires `avi:forge:uninstalled:app` when an admin removes the app, and the resolver forwards it to `/api/forge/lifecycle/uninstalled`. If that POST fails (network blip, backend rolling) the row stays in the `tenants` table. Re-installs always succeed (lazy upsert on the new FIT) but historical data from the previous install is reattached.

Recovery (only needed if you want a clean slate):

```sql
SELECT client_key, cloud_id, installed_at FROM tenants WHERE cloud_id = '<the-cloud-id>';
DELETE FROM tenants WHERE client_key = '<install-ari>';  -- CASCADE clears children
```
