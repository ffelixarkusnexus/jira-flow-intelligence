# 0009 — Engineering tooling baseline

- **Status:** accepted
- **Date:** 2026-04-29
- **Decision-makers:** the maintainer
- **Tags:** #tooling #ci

## Context and problem statement

We're moving from "build the system from a spec" to "operate the system as a funded startup would." That implies a real toolchain: lint, format, type-check, tests, coverage, CI. We need to pick the tools and the gates and document them so contributors don't relitigate every choice.

## Considered options

Many. The shortlist that matters:

- **Linters:** `ruff` (one tool) vs. `black + isort + flake8 + pyflakes + pylint` (the historical stack).
- **Type-checker:** `mypy` strict vs. `pyright` vs. nothing.
- **Coverage gate:** none, 70%, 80%, 90%.
- **Frontend lint:** `eslint` (with `next/core-web-vitals + next/typescript`) vs. nothing-but-tsc.
- **CI provider:** GitHub Actions vs. CircleCI vs. self-hosted.

## Decision

| Concern | Tool | Notes |
|---------|------|-------|
| Backend lint + format | `ruff` | One tool. Replaces black/isort/flake8. Config in `pyproject.toml`. |
| Backend type-check | `mypy` (strict) | Scoped to `backend/app/services/` and `backend/app/db/` initially. Expand later. |
| Backend tests | `pytest` + `pytest-asyncio` + `pytest-cov` | Async mode auto. |
| Backend coverage gate | **80% line, fail-under** | Set in `pyproject.toml` `[tool.coverage.report]`. Routers and seeds excluded. |
| Frontend lint | `eslint` (flat config, `next/core-web-vitals`, `next/typescript`) | Config: `frontend/eslint.config.mjs`. |
| Frontend format | `prettier` | Config: `frontend/.prettierrc.json`. |
| Frontend type-check | `tsc --noEmit` | Strict (`tsconfig.json`). |
| Frontend contract test | `next build` | Contract: prod build must succeed in CI. |
| CI | GitHub Actions | Single workflow `ci.yml`, two jobs (`backend`, `frontend`). |
| Dep updates | Dependabot weekly | `pip`, `npm`, `github-actions` ecosystems. |
| Commit format | Conventional Commits | Documented in CONTRIBUTING; not enforced server-side yet. |
| ADR format | MADR | Template at `docs/engineering/adr/0000-template.md`. |

Coverage gate of 80% is a starting point. If a PR drops below 80%, add tests, don't lower the gate.

## Consequences

- Positive: One tool per concern. Contributors install `uv` + `npm` and they have everything.
- Positive: `ruff` is fast enough that CI doesn't notice its existence. Local pre-commit isn't required.
- Positive: 80% coverage is meaningful — it forced us to write router/jira-client/AI-template tests we'd otherwise have skipped.
- Negative: `mypy` strict is currently scoped only to `services/` and `db/`. Routers and seeds aren't typed. We accepted this because expanding strictness has diminishing returns once the core is covered.
- Neutral: We don't run `next build` for frontend in PR-fast-path; it's part of the CI workflow.

## Notes

Pre-commit hooks are deliberately not added. Reasons: another install step for contributors, and CI catches the same issues. If CI feedback ever feels too slow, revisit.
