# Security

## Reporting a vulnerability

Please do **not** open a public GitHub issue for security vulnerabilities.

Instead, use GitHub's **private vulnerability reporting**: on this repository, go
to the **Security** tab → **Report a vulnerability** (under Advisories). That
opens a private advisory visible only to the maintainers. Include:

- A description of the vulnerability and its potential impact.
- Steps to reproduce, including any required configuration.
- Whether you'd like credit when the fix is announced.

## Response timeline

- **Triage** within 1 business day.
- **Acknowledgment** to the reporter with the assessed severity.
- **Patch** with a timeline proportional to severity:
  - **Critical** (data exposure, auth bypass) — same week.
  - **High** (privilege escalation, dependency CVE with active exploitation) — within two weeks.
  - **Medium** — next monthly release.
  - **Low** — backlog.
- **Customer notification** for confirmed customer-data exposure: within 72 hours per GDPR, via a published security advisory and release notes.

## Supported versions

The deployed `main` branch receives all security fixes. Production is the only version users interact with (Forge auto-upgrades installs to the latest deployed version). There are no long-lived release branches.

## Scope

In scope:

- The backend API service (`backend/app/`)
- The Forge Custom UI dashboard (`forge-prod/frontend/`)
- The Forge resolver code (`forge-prod/src/resolvers/`)
- The CDK infrastructure code (`infra/`)
- The CI/CD pipeline configuration (`.github/workflows/`)
- Dependency vulnerabilities surfaced by Dependabot

Out of scope:

- Vulnerabilities in third-party services (Atlassian Forge platform, AWS) — report those upstream.
- Issues that require an attacker to already have a valid Forge install on the target Jira site (an authorized-attacker scenario the threat model does not cover).
- Issues in the immutable product spec at `docs/jira_flow_intelligence/`.

## Posture summary

- **Authentication.** Every backend request is authenticated via Forge Invocation Token (RS256 signed by Atlassian). No header bypass; fail-closed on missing or invalid tokens.
- **Tenant isolation.** App-level `tenant_id` filtering in every query; Postgres Row-Level Security as a defense-in-depth backstop (ADR-0026); cascade-delete on uninstall.
- **Data residency.** All persistent data lives in AWS US East 1, encrypted at rest with AWS KMS.
- **Secrets.** RDS credentials and the Anthropic API key are stored in AWS Secrets Manager. No secrets in the codebase or logs.
- **Sub-processors.** AWS (hosting), Atlassian (Forge runtime), Anthropic (AI explanations — numeric signals only, no issue content). Documented in the Privacy Policy.

## Disclosure

We follow coordinated disclosure. Once a fix ships, we publicly credit reporters who request it. CVEs are filed for issues that warrant them.
