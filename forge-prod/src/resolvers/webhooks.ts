// Issue webhook + scheduled reconciliation handlers.
//
// These are Forge **triggers**, not UI/queue resolvers. Triggers invoke
// the exported function directly with `{ payload, context }` — they do
// NOT pass through @forge/resolver's `Resolver.getDefinitions()` wrapper
// (which expects a `call.functionKey` shape that triggers don't supply
// and produces `Cannot read properties of undefined (reading 'functionKey')`
// at runtime). Plain async functions are the right shape.
//
// - `avi:jira:created:issue` and `avi:jira:updated:issue` invoke
//   issueWebhookResolver, which fetches the full issue + changelog and
//   POSTs to /api/forge/sync/ingest with skip_if_stale=true.
// - `avi:jira:deleted:issue` invokes issueDeletedResolver, which DELETEs
//   the row at /api/forge/sync/issues/<id>.
// - The scheduledTrigger fires reconcileResolver daily, paginating
//   against `updated >= last_sync_at` to catch anything webhooks missed.

import api, { invokeRemote, route } from "@forge/api";

const BACKEND_REMOTE_KEY = "backend";
const RECONCILE_MAX_ISSUES = 500;
const RECONCILE_PAGE_SIZE = 100;

interface JiraIssue {
  id: string;
  key: string;
  fields: Record<string, unknown>;
  changelog?: { histories?: unknown[] };
}

// Forge triggers can deliver the issue id under at least two shapes
// depending on the event source: the typed `context.issue.id` we
// originally assumed, or a top-level `payload.issue.id` (and historically
// some events have only `issueId`). Until we have a captured payload we
// can point at, extract defensively from all three and log the raw args
// shape if none match — that gives the next CloudWatch line real
// evidence to act on instead of "no issue.id in context".
function extractIssueId(args: unknown): string | undefined {
  const a = args as {
    context?: { issue?: { id?: string }; issueId?: string };
    payload?: { issue?: { id?: string }; issueId?: string };
    issue?: { id?: string };
    issueId?: string;
  };
  return (
    a?.context?.issue?.id ??
    a?.payload?.issue?.id ??
    a?.issue?.id ??
    a?.context?.issueId ??
    a?.payload?.issueId ??
    a?.issueId
  );
}

function logUnknownPayloadShape(handler: string, args: unknown): void {
  // Truncated single-line JSON so CloudWatch shows the actual keys we
  // received. Lowered from console.error to console.warn so it stops
  // pinging the error metric filter while we collect samples.
  let snapshot: string;
  try {
    snapshot = JSON.stringify(args).slice(0, 800);
  } catch {
    snapshot = String(args).slice(0, 800);
  }
  console.warn(`${handler}: no issue.id in any known path; args=${snapshot}`);
}

const FIELDS_FOR_INGEST =
  "summary,status,issuetype,created,updated,resolutiondate,project,assignee,priority," +
  "customfield_10016,customfield_10026,customfield_10002,customfield_10004," +
  "customfield_10020,customfield_10010,customfield_10000,customfield_10001,customfield_10007";

async function fetchIssueWithChangelog(issueId: string): Promise<JiraIssue | null> {
  const res = await api
    .asApp()
    .requestJira(
      route`/rest/api/3/issue/${issueId}?fields=${FIELDS_FOR_INGEST}&expand=changelog`,
    );
  if (!res.ok) {
    console.error(`Webhook issue fetch failed: ${res.status} for issue=${issueId}`);
    return null;
  }
  return (await res.json()) as JiraIssue;
}

// ----- Issue created / updated ---------------------------------------------

export const issueWebhookResolver = async (args: unknown) => {
  const issueId = extractIssueId(args);
  if (!issueId) {
    logUnknownPayloadShape("issueWebhookResolver", args);
    return;
  }
  const issue = await fetchIssueWithChangelog(issueId);
  if (issue === null) return;

  const slim = {
    id: issue.id,
    key: issue.key,
    fields: issue.fields,
    changelog: issue.changelog,
  };
  const ingestRes = await invokeRemote(BACKEND_REMOTE_KEY, {
    path: "/api/forge/sync/ingest",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    // run_alert_eval: live ticket events tighten alert detection latency
    // (ADR-0037 entry point 2). Bulk backfill/reconcile leave it off.
    body: JSON.stringify({ payloads: [slim], skip_if_stale: true, run_alert_eval: true }),
  });
  if (!ingestRes.ok) {
    const text = await ingestRes.text();
    console.error(`Webhook ingest failed: ${ingestRes.status} ${text.slice(0, 200)}`);
  }
};

// ----- Issue deleted -------------------------------------------------------

export const issueDeletedResolver = async (args: unknown) => {
  const issueId = extractIssueId(args);
  if (!issueId) {
    logUnknownPayloadShape("issueDeletedResolver", args);
    return;
  }
  const res = await invokeRemote(BACKEND_REMOTE_KEY, {
    path: `/api/forge/sync/issues/${encodeURIComponent(issueId)}`,
    method: "DELETE",
  });
  if (!res.ok && res.status !== 404) {
    const text = await res.text();
    console.error(`Webhook delete failed: ${res.status} ${text.slice(0, 200)}`);
  }
};

// ----- Daily reconciliation ------------------------------------------------

export const reconcileResolver = async () => {
  const stateRes = await invokeRemote(BACKEND_REMOTE_KEY, {
    path: "/api/forge/sync/state",
    method: "GET",
  });
  if (!stateRes.ok) {
    console.error(`Reconcile state fetch failed: ${stateRes.status}`);
    return;
  }
  const state = (await stateRes.json()) as { lastSyncedAt: string | null };
  if (!state.lastSyncedAt) {
    console.log("Reconcile: tenant has no last_sync_at yet, skipping");
    return;
  }

  const dateBound = `"${state.lastSyncedAt.slice(0, 16).replace("T", " ")}"`;
  const jql = `updated >= ${dateBound} ORDER BY updated DESC`;
  const collected: JiraIssue[] = [];
  let nextPageToken: string | undefined;
  let pages = 0;

  while (collected.length < RECONCILE_MAX_ISSUES) {
    const res = nextPageToken
      ? await api
          .asApp()
          .requestJira(
            route`/rest/api/3/search/jql?jql=${jql}&fields=${FIELDS_FOR_INGEST}&expand=${"changelog"}&maxResults=${String(RECONCILE_PAGE_SIZE)}&nextPageToken=${nextPageToken}`,
          )
      : await api
          .asApp()
          .requestJira(
            route`/rest/api/3/search/jql?jql=${jql}&fields=${FIELDS_FOR_INGEST}&expand=${"changelog"}&maxResults=${String(RECONCILE_PAGE_SIZE)}`,
          );
    if (!res.ok) {
      console.error(`Reconcile search failed: ${res.status}`);
      return;
    }
    const page = (await res.json()) as {
      issues?: JiraIssue[];
      isLast?: boolean;
      nextPageToken?: string;
    };
    const issues = page.issues ?? [];
    collected.push(...issues);
    pages += 1;
    if (page.isLast || !page.nextPageToken || issues.length === 0) break;
    nextPageToken = page.nextPageToken;
    if (pages > RECONCILE_MAX_ISSUES / RECONCILE_PAGE_SIZE + 2) break;
  }

  if (collected.length === 0) {
    console.log("Reconcile: no issues changed since last sync");
    return;
  }

  const slim = collected.map((issue) => ({
    id: issue.id,
    key: issue.key,
    fields: issue.fields,
    changelog: issue.changelog,
  }));
  const ingestRes = await invokeRemote(BACKEND_REMOTE_KEY, {
    path: "/api/forge/sync/ingest",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ payloads: slim, skip_if_stale: true }),
  });
  if (!ingestRes.ok) {
    const text = await ingestRes.text();
    console.error(`Reconcile ingest failed: ${ingestRes.status} ${text.slice(0, 200)}`);
    return;
  }
  console.log(`Reconcile: ingested ${slim.length} issues across ${pages} pages`);
};
