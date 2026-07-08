# Handoff template — mandatory format for work-completion handoffs

> Per [CLAUDE.md rule #12](../../CLAUDE.md), every handoff to the reviewer or the maintainer uses this template. The slots cannot be skipped; "N/A" with a one-line reason is acceptable, blank is not. Handoffs in any other format will be sent back for re-format before review.

---

## What shipped

Brief 1-3 sentence summary of the work completed.

## PR / commit references

- PR #N: brief title
- Commit SHA: `<hash>` (the last SHA on main if multi-commit)
- Branch: `<branch-name>` if not yet merged

## Verification artifacts

**Match the change type to the corresponding section in [`docs/engineering/definition-of-done.md`](definition-of-done.md) and paste the artifacts here.**

### Backend / logic changes (if applicable)

- Integration test exercising the new logic — paste pytest / vitest / etc. output:

  ```
  <paste output>
  ```

- Manual verification on dev tenant — what was checked, what was observed:

  ```
  <paste here, with concrete values, not "looks right">
  ```

- Coupled-deploy status: does this change require a corresponding Forge / CDK / docs-site deploy to land at the same time? If yes, name the coordinated deploy step and who runs it.

### UI changes (if applicable)

- Screenshot of every customer-facing surface introduced or modified — attach images or paste links to the screenshots committed to the repo
- Interactive elements verified — list each new button / link / form and what was confirmed (saved, navigated, errored gracefully, etc.)

### Forge module / manifest changes (if applicable)

- Manifest diff (paste):

  ```
  <paste>
  ```

- Major-vs-minor version impact: stated with reasoning per the runbook's Forge versioning section
- Admin re-consent impact: yes / no with reasoning
- Dev-environment deploy confirmation: `forge deploy --environment development` output excerpt showing the new version succeeded
- Production deploy runbook: exact sequence the maintainer will run, with verification at each step

### Infrastructure / CDK changes (if applicable)

- `cdk diff` output (paste):

  ```
  <paste>
  ```

- Cost impact stated

## Inflight fixes (if any bugs were caught and patched during this cycle)

For each fix:

- What was wrong (root cause, not symptom)
- The commit SHA isolating the fix
- The test that now covers the regression
- The `CHANGELOG.md` entry (under `### Fixed` if customer-visible, `### Internal` if not)

If no inflight fixes: write "None."

## Risks

For each risk identified during the cycle, state ONE of:

- **Closed.** Mitigated by [specific design / lesson / ADR]. Proof: [code snippet, link, or paste].
- **Open.** Implementation does NOT mitigate. Surfacing for guidance before deploy.

Never leave a risk in "flagged but unstated" status. The middle state is the failure mode CLAUDE.md rule #12 sub-rule explicitly forbids.

## CHANGELOG.md update

Paste the entries added to `CHANGELOG.md` under `## [Unreleased]`:

```
<paste the new lines>
```

## Decisions made not explicitly covered by the prompt

For each: one-line description of the decision + the reasoning. Format:

- Decision: ... | Reasoning: ...
- Decision: ... | Reasoning: ...

If none: "None — every decision was covered by the prompt's specifications."

## Deferred items / follow-ups

Anything intentionally out of scope, surfaced for the maintainer to schedule:

- Item: ... | Reason for deferral: ... | Recommended priority: ...

If none: "None — this workstream is complete."

## Open questions for the reviewer / maintainer

Questions that the maintainer must answer before the next step proceeds. Format:

- Q1: ...
- Q2: ...

If none: "None."

---

**Self-check before submitting (delete this section before posting the handoff):**

- [ ] Every applicable section is filled in with concrete artifacts (not summarized claims)
- [ ] No risk is left in "flagged but unstated" status
- [ ] No inflight fix lacks commit + test + CHANGELOG
- [ ] Handoff would survive a strict reading by the maintainer without a corrections cycle
