# Definition of Done

> Concrete verification checklists per change type. Referenced by [CLAUDE.md rule #12](../../CLAUDE.md) ("Verification is load-bearing for 'done.'"). Use the checklist that matches the kind of change you shipped; surface in the handoff which items are done, which are N/A with reason, and never leave items blank.

## Universal items (all change types)

- [ ] Lint pass (whatever linter the codebase uses for the language touched)
- [ ] Type-check pass
- [ ] Test pass with coverage gate satisfied
- [ ] `CHANGELOG.md` entry added under `[Unreleased]` in the appropriate section (Added / Changed / Fixed / Removed / Deprecated / Security / Internal)
- [ ] ADR added if the change reflects an architectural decision (not required for routine changes)
- [ ] Handoff uses the template at [`docs/engineering/handoff-template.md`](handoff-template.md)

## Backend logic changes (math, scoring, alert evaluation, anything affecting customer-facing numbers)

In addition to universal:

- [ ] Integration test that exercises the new logic, with output pasted in the PR description AND the handoff
- [ ] Edge case tests covering: empty inputs, boundary conditions, timezone shifts (if relevant), idempotency on re-run
- [ ] Manual verification on the dev tenant: the metric / number this changes was observed to produce the expected value
- [ ] Coupled-deploy coordination: if the change introduces or modifies an endpoint that a Forge frontend consumes, the Forge deploy is part of the same coordinated landing window

## UI changes (Settings, Dashboard, Issue Panel, anything customer-visible)

In addition to universal:

- [ ] Screenshot of the rendered surface on the dev tenant, included in the PR description AND the handoff
- [ ] Manual click-through: every new interactive element verified to do what it says (settings save, button triggers expected action, navigation goes where the link claims)
- [ ] Empty state, loading state, and error state all verified (or N/A if non-applicable)
- [ ] Mobile-responsive check if the surface is reachable on mobile (or N/A if forge-app surfaces only)

## Infrastructure / CDK changes

In addition to universal:

- [ ] `cdk synth` output reviewed; no unexpected resources surfaced
- [ ] `cdk diff` output pasted in the PR description showing exactly what changes in the AWS account
- [ ] Cost impact noted (e.g., "+$0.50/mo for new Secrets Manager entry")
- [ ] Backwards-compatible: existing data / resources are preserved through the change

## Forge module / manifest changes

In addition to universal:

- [ ] Manifest diff pasted in the PR description showing exact module / function / consumer additions
- [ ] Major-vs-minor version impact analyzed and stated (per [`docs/engineering/runbook.md`](runbook.md) → Forge versioning)
- [ ] Admin re-consent impact stated (yes / no, with reasoning)
- [ ] Dev-environment deploy confirmed (`forge deploy --environment development` succeeded; new modules render on the dev tenant)
- [ ] Production deploy plan written: exact sequence of commands, verification at each step, fallback if anything surprises

## Documentation-only changes

Universal items only. Skip the change-type-specific sections.

