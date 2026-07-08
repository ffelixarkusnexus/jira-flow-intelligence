# 0003 — Default to SQLite, support PostgreSQL via optional extra

- **Status:** accepted
- **Date:** 2026-04-29
- **Decision-makers:** the maintainer
- **Tags:** #database #backend #deviation-from-spec

## Context and problem statement

`docs/jira_flow_intelligence/10_CLAUDE_CODE_MASTER/03_backend_implementation_plan.md` recommends `pip install fastapi uvicorn sqlalchemy psycopg2`. That implies PostgreSQL. We're at MVP with no live deployment, no shared database, and CI must run with no infra setup. PostgreSQL would force every contributor and CI runner to provision a database before running tests.

## Considered options

- **PostgreSQL only**, as the spec implies
- **SQLite default, PostgreSQL optional** via an extras group
- **DuckDB** (analytical workload fit, but not battle-tested as primary OLTP)

## Decision

Default `DATABASE_URL` to `sqlite:///backend/data/flow.db`. Make PostgreSQL available behind `pip install -e ".[postgres]"` (uses `psycopg[binary]`, not `psycopg2` — the v3 driver has Python 3.12+ wheels and a saner async story). All SQLAlchemy code is dialect-agnostic. The `UTCDateTime` decorator (ADR-0004) hides the SQLite tz quirk.

## Consequences

- Positive: Zero-setup local dev and CI. `uv sync && uv run pytest` works on a fresh checkout.
- Positive: Tests use `sqlite:///:memory:` with `StaticPool` — fast and isolated per test fixture.
- Positive: Pure SQLAlchemy 2.x ORM means swapping to PostgreSQL is a `DATABASE_URL` change.
- Negative: We have not actually run the system against PostgreSQL yet. The `[postgres]` extra is in `pyproject.toml`, but no CI job verifies it. Adding a Postgres CI matrix is tracked in the runbook deferred-work list.
- Negative: SQLite ignores some SQLAlchemy constraints (e.g., it's lax about CHECK), so PostgreSQL might catch issues SQLite missed.
- Neutral: This is a documented deviation from the bootstrap spec.

## Notes

Concrete deviation flagged: `psycopg2` → `psycopg[binary]`. `psycopg2` has no Python 3.13+ wheels at this writing; we'd block CI. `psycopg` v3 is the modern equivalent.
