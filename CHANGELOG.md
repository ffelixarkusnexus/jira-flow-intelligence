# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] — 2026-07-07

### Added

- Initial public release. A reference/starter implementation of a Jira Forge
  Custom UI app backed by a FastAPI engine on AWS, generalized from a working
  plugin so a reader can wire in their own Atlassian account and run it.
- **Backend** (`backend/`): FastAPI + SQLAlchemy engine — changelog-driven flow
  metrics, multi-signal bottleneck detection, threshold/trend alerting, WIP
  limits, sprint/calendar windows, CSV export. Multi-tenant with app-level
  `tenant_id` scoping plus Postgres Row-Level Security as a defense-in-depth
  backstop. Alembic migrations; deterministic engine with AI used only for
  one-sentence explanations.
- **Forge app** (`forge-prod/`): Custom UI dashboard (Vite + React + Tailwind)
  and resolvers, calling the backend via Forge Remote with Forge Invocation
  Token (FIT) authentication.
- **Infrastructure** (`infra/`): AWS CDK (Python) stacks — ECR, network, RDS
  data, App Runner compute, and observability (CloudWatch alarms + Route 53
  health check). Alert email and prod healthz host are deploy-configurable.
- **CI** (`.github/workflows/`): path-aware pipeline running backend
  lint/type/test, a Postgres RLS smoke test, the Forge build, and infra synth +
  tests.
- Engineering documentation: Architecture Decision Records, an operator
  runbook, an end-user manual, and a setup tutorial (`docs/SETUP.md`).
