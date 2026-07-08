## What

<!-- Briefly: what does this PR change? One or two sentences. -->

## Why

<!-- The reason behind the change. Link the issue if there is one. -->

## How

<!-- Notable implementation choices, especially anything non-obvious. Skip for trivial changes. -->

## CHANGELOG entry (required)

Add a one-line bullet under `## [Unreleased]` in [`CHANGELOG.md`](../CHANGELOG.md). Pick the section that fits:

- [ ] **Added** — new customer-visible feature
- [ ] **Changed** — change to existing customer-visible behavior
- [ ] **Fixed** — bug fix affecting customers
- [ ] **Deprecated** / **Removed** / **Security**
- [ ] **Internal** — engineering hygiene, refactor, CI, dependency bump, docs (use this for anything not customer-visible)

If this PR is customer-visible (`Added` / `Changed` / `Fixed` / `Security`), also draft a one-sentence release-notes line below — the reviewer will refine for the partner console.

**Draft release-notes line for the Marketplace listing:**

> _..._

See [`docs/engineering/release-process.md`](../docs/engineering/release-process.md) for the full policy and how CHANGELOG, release notes, and ADRs relate.

## ADR

<!-- If this PR makes a non-trivial design decision, link the ADR. If it modifies an existing one, link it. -->

- [ ] N/A — no architectural decision in this PR
- [ ] Adds: `docs/engineering/adr/NNNN-...md`
- [ ] Modifies: `docs/engineering/adr/NNNN-...md`

## Checks

- [ ] `uv run ruff check backend` clean
- [ ] `uv run ruff format --check backend` clean
- [ ] `uv run mypy` clean
- [ ] `uv run pytest --cov` passes (≥80%)
- [ ] `cd frontend && npm run lint && npm run typecheck && npm run format:check && npm run build` clean
- [ ] Glossary / runbook updated if relevant
- [ ] CHANGELOG entry added under `[Unreleased]`

## Verification (required — match the relevant section in [`docs/engineering/definition-of-done.md`](../docs/engineering/definition-of-done.md))

Paste the verification artifacts here. Per [CLAUDE.md rule #12](../CLAUDE.md), "tests pass locally" alone does not satisfy this — the actual outputs go in this section.

**For backend logic changes:**
- Test command run: `<exact command>`
- Test output (paste relevant excerpt):

  ```
  <paste here>
  ```

- Manual verification on dev tenant (what was checked, what value was observed):

  ```
  <paste here>
  ```

**For UI changes:**
- Screenshot of the rendered surface on the dev tenant (paste image link or attach):

  > <attach screenshot>

- Interactive elements verified (list what was clicked and what happened):

  ```
  <paste here>
  ```

**For infrastructure / Forge changes:**
- `cdk diff` or manifest diff (paste):

  ```
  <paste here>
  ```

- Coupled-deploy status: which other systems must deploy together with this PR?

  ```
  <paste here>
  ```

**Definition of Done — confirm the relevant section was followed:**

- [ ] Universal items satisfied
- [ ] Change-type-specific items satisfied (see [`docs/engineering/definition-of-done.md`](../docs/engineering/definition-of-done.md))
- [ ] CHANGELOG.md entry added
- [ ] Handoff (if applicable) uses [`docs/engineering/handoff-template.md`](../docs/engineering/handoff-template.md)
