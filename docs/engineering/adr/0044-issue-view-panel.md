# 0044 — Issue View Panel: surface per-issue time-per-status inside the Jira issue view

- **Status:** Accepted
- **Date:** 2026-06-06
- **Decision-makers:** the maintainer; Claude Code drafted
- **Tags:** #forge #ux-surface #per-issue #read-only
- **Related:** [ADR-0019](0019-pivot-to-forge.md) (the Forge platform this extends); [ADR-0042](0042-pause-statuses-and-bottleneck-attribution-scope.md) (external-blocking marker the panel surfaces)

## Context and problem statement

Jira Flow Intelligence today lives only on the Jira project page. An engineer triaging a specific ticket — *"why has this been in Code Review for so long?"* — is looking at the Jira issue view, not the project dashboard. To get our analytics for that ticket, they have to navigate to the project page, find the ticket, and dig in. They don't. They make decisions without our data.

Surfacing an issue's flow data directly in the Jira issue view — where the engineer is already looking during triage — is a well-established UX pattern for issue-panel plugins.

The panel is **read-only**: it reuses backend data we already compute from the changelog. It does not add scoring, mutations, or net-new computation — it surfaces existing data in the place the engineer is actually looking.

## Considered options

1. **Don't ship — keep Jira Flow Intelligence on the project page only.** Rejected — concedes a category UX standard without engineering reason. The data already exists; the surface is the missing piece.
2. **`jira:issuePanel` Forge module (right-side panel on the issue view).** Adopted. The standard Forge surface for per-issue read-only data.
3. **`jira:issueActivity` Forge module (a tab on the issue view).** Considered. The Activity tab is more discoverable than the panel but consumes more user attention. The data fits a panel cleanly (one header, one small table, one badge); a tab feels heavier than the data warrants.
4. **Custom Jira app icon + modal.** Rejected — non-standard surface, extra clicks, no Forge benefit.

## Decision

Add a `jira:issuePanel` Forge module rendering per-issue time-per-status data on the right side of every Jira issue view.

### Forge manifest

```yaml
modules:
  jira:issuePanel:
    - key: flow-intelligence-issue-panel
      resource: main
      resolver:
        function: issuePanelResolver
      title: Jira Flow Intelligence
      icon: <existing icon path>
  function:
    - key: issuePanelResolver
      handler: index.issuePanelResolver
```

No new scope. The panel reads issue context that's already available to any Forge app installed on the project; no `read:` scope addition is required. Admin re-consent is **not** triggered.

### Resolver (`forge-prod/src/resolvers/issuePanel.ts`)

```typescript
import Resolver from '@forge/resolver';
import { invokeRemote } from '../lib/remote';

const resolver = new Resolver();
resolver.define('getIssueData', async ({ context }) => {
  const issueKey = context.extension.issue.key;
  return invokeRemote(`/api/forge/issue/${issueKey}/panel-data`);
});
export const handler = resolver.getDefinitions();
```

Pure pass-through: extract `issueKey` from Forge context, fetch from the backend, return.

### Backend endpoint (`GET /api/forge/issue/{issue_key}/panel-data`)

Returns:

```typescript
type IssuePanelData = {
  issueKey: string;
  currentStatus: string;
  statusHistory: Array<{
    status: string;
    enteredAt: string;
    exitedAt: string | null;  // null = current slice
    durationSeconds: number;  // honors active work schedule (ADR-0043) if set
    isExternalBlocking: boolean;  // ADR-0042 marker
  }>;
  totalCycleTimeSeconds: number;
  isInCurrentBottleneck: boolean;
  projectDashboardUrl: string;  // deep link to the project dashboard, scoped to this issue
};
```

Implementation: load the issue's `time_slices` rows scoped by tenant, map to the response shape (applying ADR-0043 working-time math when active, marking ADR-0042 external-blocking statuses), and compute `isInCurrentBottleneck` by comparing the issue's current status to the project's currently-named bottleneck.

### Frontend (`forge-prod/frontend/src/IssuePanel.tsx`)

Read-only, single-column layout matching the existing Jira Flow Intelligence visual language:

- Header: *"Time in status: {humanized total cycle time}"*.
- Status history table: entered timestamp, status, humanized duration, external-blocking marker if applicable.
- Bottleneck-contribution badge (only when `isInCurrentBottleneck`): *"This ticket is currently in the project's named bottleneck: {status}"*.
- Deep link: *"Open in Jira Flow Intelligence →"* to the project dashboard scoped to this issue.

No charts in the panel — charts belong on the project page. The panel is a focused triage surface.

## Consequences

### Positive

- Jira Flow Intelligence's value reaches the engineer *in the surface they're already looking at* during ticket triage — the data is one panel away from the issue body, instead of requiring a trip to the project dashboard.
- Read-only design + no new scopes means: no admin re-consent, no new auth path, no new write surface to secure. The panel is purely additive.
- Reuses existing backend data (`time_slices`). No new computation; one endpoint maps existing rows to a panel-shaped response.
- Surfaces the ADR-0042 external-blocking marker in the smallest, clearest place — directly next to the status that has the marker. Reinforces the *"time still tracked, attribution excluded"* model for users.

### Neutral

- One additional Forge module + one additional resolver + one additional backend endpoint + one additional frontend component. Surface area for testing is small; integration risk is low because the data path is read-only and well-defined.

### Negative

- Adding a `jira:issuePanel` module without OAuth scope additions is **observed to be a minor Forge version bump** (per the runbook's empirical correction 2026-05-27 — adding event/scheduled-trigger/consumer modules has all been MINOR; the `jira:issuePanel` module sits in the same documented-minor category as resolver code and Custom UI bundle changes). No admin re-consent expected. If a surprise major bump occurs, the operating-rule from ADR-0019 + the runbook applies (schedule off the critical path).

## Tests proving the change

- `backend/tests/test_issue_panel.py` — endpoint returns correct status history for a mixed-status issue; external-blocking marker correctly attached; `isInCurrentBottleneck` correctly computed.
- `forge-prod/frontend/src/IssuePanel.test.tsx` — panel renders with mock data; external-blocking marker shown for paused statuses; bottleneck badge shown only when applicable; deep link includes correct issue scope.

## Cross-references

- [ADR-0019](0019-pivot-to-forge.md) — the Forge platform constraint this extends within.
- [ADR-0042](0042-pause-statuses-and-bottleneck-attribution-scope.md) — the external-blocking marker the panel surfaces.
- [ADR-0043](0043-work-schedule-and-working-time-duration-math.md) — the working-time math the panel applies when active.
