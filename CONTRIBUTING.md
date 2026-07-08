# Contributing

Thanks for working on Flow Intelligence. This is a tight project; the rules here are short on purpose.

## Prerequisites

- Python ≥ 3.12 (we test against 3.12 and 3.13)
- Node ≥ 20
- [uv](https://github.com/astral-sh/uv) for Python deps and virtualenv

## Setup

```bash
uv sync
cd frontend && npm install && cd ..
```

That's it. The default DB is SQLite at `backend/data/flow.db`; no infra to provision.

## Daily loop

```bash
# Backend
uv run ruff check backend                 # lint
uv run ruff format backend                # auto-format
uv run mypy                               # type-check
uv run pytest --cov                       # tests + 80% coverage gate

# Frontend
cd frontend
npm run lint
npm run typecheck
npm run format:check
npm run build                             # contract test
```

CI runs all of the above on every PR. If something fails locally and you can't fix it, **don't bypass it** — open a draft PR and ask for help. We never ship with `--no-verify`.

## Workflow

1. Cut a branch from `main`. Naming convention: `feat/...`, `fix/...`, `docs/...`, `chore/...`.
2. Make the change. Keep PRs scoped to one concern.
3. Run the daily loop above. CI must be green before review.
4. Open the PR. Fill in the template. Link any ADR you wrote or modified.
5. Get one approval. Squash-merge.

## Commits

We use [Conventional Commits](https://www.conventionalcommits.org/). Examples:

- `feat(insights): add p95 to status window metrics`
- `fix(slicing): treat negative durations from corrupt changelog as zero`
- `docs(adr): add 0010 on background scheduler`
- `chore(deps): bump pydantic to 2.13`
- `test(routers): cover insights endpoint with explanation disabled`

Not enforced by a hook (yet). Reviewers will gently nudge.

## When you make a non-trivial decision

Write an ADR. See `docs/engineering/adr/README.md` and copy `0000-template.md`. Trivial code-level choices (variable names, helper layout, fixture shapes) don't need ADRs. Anything that has a viable alternative we didn't take, is non-obvious from the code, or future-us will want to remember — that's an ADR.

If you're unsure, write the ADR. It's cheap.

## What stays out of this repo

- Real Jira credentials (use `.env` locally; CI gets test fixtures, never live tokens).
- The SQLite DB file (`backend/data/*.db` is gitignored).
- Anything from `docs/jira_flow_intelligence/` modified in-place — that's the immutable product spec. If it's wrong, raise an issue, don't quietly edit.

## Reporting issues

Bugs / feature requests: open a GitHub issue.
Security vulnerabilities: see [SECURITY.md](SECURITY.md) — do not file a public issue.
