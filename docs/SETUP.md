# Setup

A from-zero walkthrough to run Jira Flow Intelligence against your own Atlassian
account. Everything you fill in maps to a row in the
[Configure your own values](../README.md#configure-your-own-values) table in the
README — keep that open alongside this guide.

Prerequisites: [Node.js](https://nodejs.org/) 22+, [uv](https://docs.astral.sh/uv/)
(Python package manager), and `git`. AWS is optional (step F).

> **You do NOT need the Atlassian Marketplace to use this.** `forge install`
> puts the app directly on any Jira site you administer — your free dev site, or
> your company's site — for personal or internal use. The Marketplace is only for
> distributing to **other** organizations; if that's your goal, see
> [PUBLISHING.md](PUBLISHING.md).

## A. Create a free Atlassian developer account + Cloud dev site

1. Sign up at <https://developer.atlassian.com/> (free).
2. Create a **free Cloud developer site** at
   <https://go.atlassian.com/cloud-dev> — you get a `your-site.atlassian.net`
   Jira instance to install the app into. Add a project and a few issues so the
   dashboard has data to chart.

## B. Install the Forge CLI and register the app

```bash
npm install -g @forge/cli
forge login          # uses your Atlassian email + an API token

cd forge-prod
npm install
forge register       # names the app and mints its ARI
```

`forge register` prints the app id — an ARI like
`ari:cloud:ecosystem::app/00000000-0000-0000-0000-000000000000`. Paste it into
**two** places:

- `forge-prod/manifest.yml` → `app.id` (replaces `REPLACE-WITH-YOUR-APP-ID`).
- Your backend `.env` → `FORGE_APP_ID` (same value; it's the FIT audience).

## C. Get a Jira API token and fill `.env`

1. Create an API token at
   <https://id.atlassian.com/manage-profile/security/api-tokens>.
2. Copy the template and fill it in:

   ```bash
   cp .env.example .env
   ```

   Set at least `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_JQL`, and
   `FORGE_APP_ID`. For email, either set `RESEND_DRY_RUN=1` (logs sends, hits no
   network) or provide a `RESEND_API_KEY` + verified-domain addresses. Every
   field is explained in `.env.example` and the README config table.

## D. Run the backend locally

```bash
uv sync

# Apply migrations (SQLite by default — no database server needed for local dev)
DATABASE_URL=sqlite:///backend/data/flow.db PYTHONPATH=backend uv run alembic upgrade head

# Run it
PYTHONPATH=backend uv run uvicorn app.main:app --reload
```

The API is now at <http://localhost:8000> (docs at `/docs`). Leaving
`FORGE_APP_ID` empty runs the backend without Forge auth — handy for poking the
engine locally with `curl`; never deploy it that way.

To reach the backend from inside Jira during development, run `forge tunnel`
(next step) or expose it with a tunnel (ngrok / Cloudflare Tunnel) and point the
manifest `remotes[].baseUrl` at that URL.

## E. Deploy the Forge app and install it on your dev site

```bash
cd forge-prod
npm run tsc                              # build resolvers -> dist/
cd frontend && npm run build && cd ..    # build Custom UI -> static/main/

forge deploy --environment development
forge install --environment development --site your-site.atlassian.net --product Jira --confirm-scopes
```

Open your project in Jira → the **Flow Intelligence** page appears on the
project sidebar. Use the **Settings tab → Load demo data** button (dev only) to
populate synthetic issues if you want to see fully-formed charts immediately.

> **Both** `npm run tsc` and `npm run build` are mandatory before every deploy —
> Forge packages whatever is in `dist/` and `static/main/` without rebuilding.
> See the [runbook](engineering/runbook.md) for the full deploy semantics.

That `forge install` is all you need for personal or internal use — the app now
runs on your own Jira site with no Marketplace listing involved. Distributing to
**other** organizations is a separate step; see [PUBLISHING.md](PUBLISHING.md).

## F. Optional — deploy the backend to AWS with CDK

For a real deployment (instead of a local tunnel), the `infra/` CDK app
provisions ECR, VPC, RDS Postgres, App Runner, and observability.

```bash
cd infra
uv sync
AWS_ACCOUNT_ID=... AWS_REGION=us-east-1 uv run cdk bootstrap
uv run cdk deploy -c env=dev
```

Configure the deploy-time infra values (also in the README config table):

- `AWS_REGION` — your region.
- `ALERT_EMAIL` — recipient for the ops-alert SNS topic. Passed as an env var or
  `-c alert_email=...`; surfaced as the `AlertEmailRecipient` stack output, which
  you then subscribe manually (see the runbook's "Operational alerting" section).
- `HEALTHZ_HOST` — the public host Route 53 pings for the prod `/healthz` check
  (`-c healthz_host=...`).

## G. CI/CD & automated deploys (optional)

The repo ships two GitHub Actions workflows:

- **`ci.yml`** — the test suite: backend lint / type-check / tests, a Postgres
  smoke test, the Forge build, and infra `cdk synth` + tests. Runs on every pull
  request. This is the only workflow that does anything on this reference repo.
- **`deploy.yml`** — a CDK + Forge deploy that fires after CI succeeds on
  `main` / `develop` / `feature/**`. Its deploy jobs are **gated off by default**
  behind a `DEPLOY_ENABLED` repository variable, so the workflow never runs a real
  deploy unless you explicitly opt in — on this repo and on every fork alike.

> **Caveat — this reference repo is not connected to any live infrastructure.**
> No AWS account, GitHub environments, or deploy credentials are configured, and
> `DEPLOY_ENABLED` is unset, so `deploy.yml`'s deploy jobs are skipped — **CI runs
> the test suite only.** Nothing in this repo touches live infrastructure until
> *you* wire it up on your own copy.

To enable automated deploys on **your own** repository:

1. **`scripts/aws-bootstrap.sh`** — creates the GitHub OIDC identity provider and
   the deploy IAM role in your AWS account. Set `<YOUR_AWS_ACCOUNT_ID>` and
   `<YOUR_GITHUB_ORG>` at the top of the script first.
2. **`scripts/github-environments.sh`** — creates the `dev` / `staging` / `prod`
   GitHub environments and sets the AWS account/region variables + the
   deploy-role secret on each.
3. **Set the `DEPLOY_ENABLED` repository variable to `true`** — Settings → Secrets
   and variables → Actions → **Variables** → New repository variable. This is the
   opt-in switch; without it the deploy jobs stay skipped.

After that, pushes to the mapped branches deploy automatically.

After deploy, point `forge-prod/manifest.yml` → `remotes[].baseUrl` at the
backend URL (the `BackendUrl` stack output or your custom domain), then redeploy
the Forge app. The [runbook](engineering/runbook.md) covers first-time AWS
setup, the Resend API key, and production operations in detail.
