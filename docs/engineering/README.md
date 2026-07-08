# Engineering docs

Engineering-practice docs for this repo. The product/domain spec lives in `../jira_flow_intelligence/` — that tree is immutable bootstrap input. Don't edit it; raise issues if it's wrong.

## What's here

- [`adr/`](adr/) — Architectural Decision Records (MADR format). Read the index in [`adr/README.md`](adr/README.md).
- [`plans/`](plans/) — Phase / initiative plans. Each plan is a living doc tracking acceptance criteria, workstreams, risks, and decisions-as-we-go.
- [`runbook.md`](runbook.md) — How to set up, operate, debug. The "Deferred work" section lists known gaps for production readiness.
- [`glossary.md`](glossary.md) — Authoritative definitions of domain terms used in code and ADRs.

## When to add to this directory

| You did this | Write |
|--------------|-------|
| Made a non-trivial design choice | a new ADR |
| Started or scoped a new initiative (phase, big feature, migration) | a plan in `plans/` |
| Found a new operational pattern (debug recipe, common pitfall) | runbook section |
| Introduced a new domain term, or sharpened an existing one | glossary entry |
| Wrote a quick how-to ("how to seed a custom dataset") | runbook section |

YAGNI applies. Don't add docs for things we don't have. If a doc starts to rot, delete it — empty is better than wrong.
