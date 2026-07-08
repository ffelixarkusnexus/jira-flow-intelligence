# Release process

This is the **policy** document for releasing Jira Flow Intelligence — what counts as a change, where each change gets logged, how versions get bumped, and the discipline that keeps the three release artifacts (CHANGELOG, customer-facing release notes, ADRs) consistent. Operational mechanics of `forge deploy`, partner-console editing, and CI/Deploy pipeline behavior live in [`runbook.md`](runbook.md); this doc tells you the rules, the runbook tells you the keystrokes.

## The three release artifacts

| Artifact | Path | Audience | Source of truth for | Updated when |
|---|---|---|---|---|
| **CHANGELOG** | [`/CHANGELOG.md`](../../CHANGELOG.md) | Internal (engineers, future contributors, AI sessions) | What shipped in each version — both customer-visible and internal | **At PR time** — every PR adds an entry under `## [Unreleased]` |
| **Customer release notes** | your Marketplace listing (Atlassian Partner Console → Versions → Release notes field) | Customers | A customer-facing rewrite of customer-visible changes per version | **Within ~24h of a customer-visible Forge production deploy** |
| **ADRs** | [`docs/engineering/adr/`](adr/) | Internal — durable record of decisions | Why a particular architectural choice was made between alternatives | **When a non-trivial decision is made** (CLAUDE.md "How we make decisions here"). Not every change gets an ADR; routine changes don't. |

The relationship in one sentence: **CHANGELOG captures every change; the customer-facing release notes are the customer-facing rewrite of CHANGELOG's customer-visible sections; ADRs capture the architectural reasoning behind whichever changes warrant a recorded decision.**

## CHANGELOG section conventions

Per [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/), with one local addition:

- **Added** — new customer-visible features
- **Changed** — changes to existing customer-visible behavior
- **Fixed** — bug fixes affecting customers
- **Deprecated** — features marked for future removal
- **Removed** — features actually removed
- **Security** — security-relevant fixes
- **Internal** — engineering hygiene, CI, refactors, dependency bumps, internal architectural improvements, documentation. Local addition; the standard Keep a Changelog spec doesn't include this section, but the discipline this doc enforces requires capturing internal-only changes alongside customer-visible ones so the full change set per version lives in one place.

The first six are the source for customer-facing release notes. The `Internal` section is not surfaced to customers.

## When to bump versions

**You don't pick the version — Forge does.** `forge deploy --environment production` inspects what changed in the manifest and auto-decides minor vs. major. The decision rules and empirical observations live in the runbook under [Forge versioning — what `forge deploy` actually creates](runbook.md#forge-versioning--what-forge-deploy-actually-creates).

Short summary table — what kind of change maps to what kind of release work:

| Change type | Version impact | CHANGELOG section | Customer release notes? | ADR required? |
|---|---|---|---|---|
| New customer-visible feature | Forge minor (auto) | `Added` | Yes | If architectural |
| Bug fix affecting customers | Forge minor (auto) | `Fixed` | Yes if first-impression visible | Usually no |
| Behavior change affecting customers | Forge minor (auto) | `Changed` | Yes | Usually yes |
| Internal refactor, no behavior change | Forge minor (auto) | `Internal` | No | If architectural |
| CI / coverage / lint improvement | No Forge deploy needed | `Internal` | No | No |
| Documentation update | No Forge deploy needed | `Internal` | No | No |
| New Forge OAuth scope OR `licensing.enabled` flip OR `webtrigger` module | **Forge MAJOR** | `Added` / `Changed` | Yes (admin re-consent required) | Yes, always |

Notes:

- Backend-only deploys (App Runner) and docs-site deploys (S3 + CloudFront) **do not bump the Forge version**. They land under `[Unreleased]` in the CHANGELOG and ship to customers as part of the next Forge production deploy's release notes.
- Adding event triggers (`avi:jira:*`), `scheduledTrigger` modules, or `consumer:` modules was historically thought to force a major bump — confirmed empirically 2026-05-27 to be **minor**. See the runbook's table for the documented + observed mapping.

## PR-time discipline

Every PR adds at least one bullet to the `## [Unreleased]` section of `CHANGELOG.md`. The [pull request template](../../.github/PULL_REQUEST_TEMPLATE.md) prompts for this explicitly:

- Pick the section that fits (`Added` / `Changed` / `Fixed` / `Internal` etc.).
- One-line bullet. Optional sub-bullets if there's more to say. Reference the PR number (`#NN`) or the commit SHA in parentheses.
- For customer-visible changes (`Added` / `Changed` / `Fixed` / `Security`): also draft a one-sentence customer-facing release-notes line in the PR description; refine it for the partner console before publishing.

CI **does not** gate on missing CHANGELOG entries. That would be overkill for a small project, and the template makes the omission visible enough. The principle is honest discipline, not automated enforcement.

## Deploy-time workflow

When `forge deploy --environment production` ships a new version:

1. **Identify the version.** Forge reports the auto-decided version (e.g., `6.3.0`).
2. **Cut the CHANGELOG.** In `CHANGELOG.md`, rename `## [Unreleased]` to `## [<version>] — <YYYY-MM-DD>` and insert a fresh empty `## [Unreleased]` block above it. The just-cut version's sections are the change set for that release.
3. **Surface customer-visible changes** from the just-cut version — the `Added` / `Changed` / `Fixed` / `Security` sections are the source.
4. **Draft** the customer-facing release notes for the version and keep them with your release records.
5. **Review, approve, and paste** the approved text into the Atlassian Partner Console → app → Versions → [version] → Release notes field. Per the runbook, App-root Versions → Release notes edits auto-publish (no Atlassian re-review).

For a backend or docs-site deploy that does NOT bump the Forge version: skip steps 1–5. The change is already in `[Unreleased]`; it ships to customers on the next Forge deploy.

## What goes in an ADR vs. the CHANGELOG

CHANGELOG and ADRs answer different questions:

- **CHANGELOG**: *"What shipped in this version?"* — durable log, ordered by version, exhaustive within the project's start point.
- **ADR**: *"Why did we choose X over Y?"* — durable record of a specific decision, ordered by ADR number, only created when a non-trivial decision is made.

A change in the CHANGELOG may or may not have an associated ADR. A bug fix usually doesn't need one. A new external dependency, a schema change, a deviation from spec, or a chosen-among-alternatives architectural approach does. See CLAUDE.md "How we make decisions here" for the trigger.

When a CHANGELOG entry has an associated ADR, reference it in the bullet: `(ADR-NNNN)`. This lets a future reader walk from the changelog → to the decision record → to the reasoning, in three steps.

## Quality bar for CHANGELOG entries

- **One-line bullet.** Multi-paragraph entries belong in ADRs, not the changelog.
- **Customer impact named, when relevant.** "Fixed X" without "so that Y" is half the work for customer-visible entries.
- **No internal vocabulary in customer-visible sections.** `compute_trends`, `TenantContext`, `_persist` — those go under `Internal`. Customer-visible bullets use customer-facing names ("the bottleneck card", "the alerts panel").
- **PR / commit reference.** Always end the bullet with `(#NN)` or `(commit <sha>)`. Lets future readers trace the line back to the code.
- **Honest framing.** Don't soften a regression-fix into a "performance improvement." Customers see through the corporate weasel-speak; engineers reading the log six months later need to know what actually happened.

## What about retroactive entries?

The CHANGELOG starts at version 6.2.0 / 6.3.0. Versions before that are intentionally not backfilled — older history lives in the ADRs in [`docs/engineering/adr/`](adr/), and a customer asking "what changed in version 5.x" is answered by the ADR set, not by this file.

Do not backfill pre-6.2.0 versions. If you find yourself tempted to, the answer is "the ADRs already cover the architectural decisions; trying to reconstruct a per-version change log from git archeology will produce a noisier record than the ADRs already do."

## Cross-references

- Operational mechanics of Forge versioning, deploy semantics, auto-publish behavior, listing-copy field locations: [`runbook.md`](runbook.md) — specifically the "Forge versioning", "Release notes per version", "Listing copy fields", and "What auto-publishes to Marketplace vs. what needs review" subsections.
- All architectural decisions: [`docs/engineering/adr/`](adr/).
- PR template enforcing this discipline: [`.github/PULL_REQUEST_TEMPLATE.md`](../../.github/PULL_REQUEST_TEMPLATE.md).
- Keep a Changelog spec: [keepachangelog.com/en/1.1.0/](https://keepachangelog.com/en/1.1.0/).
