import api, { invokeRemote, route } from "@forge/api";
import Resolver from "@forge/resolver";

import { enqueueBackfill } from "./backfill";

// Forge resolver functions hard-cap at 25 seconds — we have to fit
// pagination + the backend POST + metric recomputation into that. Two
// pages of 100 issues, 30-day window, fits comfortably (~10s per page +
// ingest). Larger backfills want a proper async pattern (Forge consumer /
// backend queue), tracked as Phase 3.B.
const PAGE_SIZE = 100;
const MAX_ISSUES = 200;
const JQL_DATE_BOUND = "-30d";
// Matches the `remotes[].key` in manifest.yml. invokeRemote routes through
// the Forge runtime, which attaches a Forge Invocation Token to the
// outbound call so the backend can validate it against Atlassian's JWKS.
const BACKEND_REMOTE_KEY = "backend";

interface JiraIssue {
  id: string;
  key: string;
  fields: Record<string, unknown>;
  changelog?: { histories?: unknown[] };
}

interface JiraSearchResponse {
  issues: JiraIssue[];
  startAt?: number;
  maxResults?: number;
  total?: number;
  isLast?: boolean;
  nextPageToken?: string;
}

const resolver = new Resolver();

resolver.define("getContext", async ({ context }) => {
  const siteUrl = (context as { siteUrl?: string }).siteUrl;
  // `environmentId` is documented on @forge/bridge's FullContext (per
  // node_modules/@forge/bridge/out/types.d.ts:32). The Forge resolver
  // runtime exposes the same FullContext shape on the resolver context
  // — `environmentType` is already accessed this way below, this
  // mirrors the cast pattern. Required for the deep-link URL form
  // `/jira/{projectType}/projects/{key}/apps/{appId}/{envId}` per
  // developer.atlassian.com.
  const envId = (context as { environmentId?: string }).environmentId;

  // 2026-06-08 customer-facing URL fix: push siteUrl AND envId to the
  // backend so `tenant.display_url` + `tenant.forge_env_id` get populated
  // on every dashboard mount. The backend's lifecycle handler only sees
  // the FIT (which carries the install identity, not these display
  // fields), so without this push:
  //   - every customer-facing URL would build from the canonical
  //     `cloud-{uuid}` form and route to Atlassian's "Page unavailable"
  //     page (the 2026-06-08 bug), AND
  //   - the deep-link URL pattern can't be constructed without envId,
  //     leaving emails on the transitional /boards URL that's one
  //     Jira-side click shy of Jira Flow Intelligence.
  // Backend endpoint is idempotent + no-ops when value is unchanged;
  // single missed push is recovered on the next mount.
  if (siteUrl || envId) {
    try {
      await invokeRemote(BACKEND_REMOTE_KEY, {
        path: "/api/forge/sync/display-url",
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          display_url: siteUrl ?? null,
          env_id: envId ?? null,
        }),
      });
    } catch (e) {
      // Non-fatal — the dashboard still loads even if these fields
      // never get persisted. Worst case: emails fall back to the
      // canonical URL until the next successful mount.
      console.warn("Failed to push siteUrl + envId to backend:", e);
    }
  }

  return {
    cloudId: context.cloudId,
    installationId: context.installContext,
    projectKey: context.extension?.project?.key,
    // siteUrl powers click-to-open-in-Jira on the WIP Aging chart.
    // Forge populates this on the resolver context for product modules.
    siteUrl,
    // Forge environment the install is on: "development" | "staging" |
    // "production" (lowercased for stable string comparison in the UI).
    // Drives UI gates for dev-only features like the DemoSeedPanel —
    // customers on the Marketplace production env must never see those
    // panels regardless of backend state. Source verified 2026-05-26:
    // Forge runtime context exposes `environmentType`; process.env
    // approaches don't work (community post #78345).
    environmentType:
      (context as { environmentType?: string }).environmentType?.toLowerCase() ?? null,
  };
});

// Sprint custom-field IDs vary per Jira site. We try a static fallback
// chain (covers ~95% of Cloud sites) plus runtime discovery via
// /rest/api/3/field — finds the field whose schema.custom matches Jira
// Software's sprint type marker, regardless of its actual ID. One extra
// Jira call per sync; ~200 fields in the response, fast.
async function discoverSprintFieldId(): Promise<string | null> {
  try {
    const res = await api.asUser().requestJira(route`/rest/api/3/field`);
    if (!res.ok) return null;
    const fields = (await res.json()) as Array<{
      id: string;
      name?: string;
      schema?: { custom?: string };
    }>;
    const match = fields.find(
      (f) => f.schema?.custom === "com.pyxis.greenhopper.jira:gh-sprint",
    );
    return match?.id ?? null;
  } catch {
    return null;
  }
}

resolver.define("syncJira", async ({ payload }: { payload?: { force?: boolean } }) => {
  // /rest/api/3/search/jql refuses unbounded queries — every search must
  // include a restriction. First sync uses the 30-day fallback; subsequent
  // syncs use the tenant's last_sync_at so we only fetch changed issues.
  // The `force` arg ignores last_sync_at and re-pulls the full
  // 30-day window — useful after a schema change that added new
  // fields to the request, where existing rows wouldn't otherwise be
  // re-touched until Jira itself updated them.
  const force = payload?.force === true;
  let lastSyncedAt: string | null = null;
  if (!force) {
    const stateRes = await invokeRemote(BACKEND_REMOTE_KEY, {
      path: "/api/forge/sync/state",
      method: "GET",
      headers: { "Content-Type": "application/json" },
    });
    if (stateRes.ok) {
      const state = (await stateRes.json()) as { lastSyncedAt: string | null };
      lastSyncedAt = state.lastSyncedAt;
    }
  }

  const dateBound = lastSyncedAt
    ? // Jira JQL: `updated >= "yyyy-MM-dd HH:mm"`. Strip the seconds + zone
      // off the ISO string and quote it.
      `"${lastSyncedAt.slice(0, 16).replace("T", " ")}"`
    : JQL_DATE_BOUND;
  const jql = `updated >= ${dateBound} ORDER BY updated DESC`;
  // assignee + priority + the four most-common Jira Software story-points
  // custom field IDs feed the WIP Aging chart. Different Jira sites
  // use different IDs (10016 is the post-2023 Cloud default; 10026/10002/
  // 10004 are common on older sites). Backend's _extract_story_points
  // tries them in order and uses the first one with data. Per-tenant
  // explicit override moves to settings UI.
  // Sprint custom field IDs (Path 3): we read sprint metadata + issue
  // membership directly from the Sprint custom field on each issue, so no
  // separate `/rest/agile/1.0/board` calls (and no manifest scope re-grant).
  // Static fallback chain catches the common Cloud defaults; runtime
  // discovery via /rest/api/3/field catches custom configurations.
  const discoveredSprintField = await discoverSprintFieldId();
  const fieldList = [
    "summary",
    "status",
    "issuetype",
    "created",
    "updated",
    "resolutiondate",
    "project",
    "assignee",
    "priority",
    // Story-points fallback chain.
    "customfield_10016",
    "customfield_10026",
    "customfield_10002",
    "customfield_10004",
    // Sprint static fallback chain.
    "customfield_10020",
    "customfield_10010",
    "customfield_10000",
    "customfield_10001",
    // Dynamically discovered Sprint field (often the same as one of the
    // above; including both is harmless — Jira dedupes server-side).
    ...(discoveredSprintField ? [discoveredSprintField] : []),
  ];
  const fields = fieldList.join(",");
  const expand = "changelog";
  const maxResults = String(PAGE_SIZE);

  const collected: JiraIssue[] = [];
  let nextPageToken: string | undefined;
  let pages = 0;

  while (collected.length < MAX_ISSUES) {
    // The route tag forbids passing a pre-built URL string in a single
    // template slot (treats it as path manipulation). Each query-param
    // value goes through its own ${} so Forge can validate.
    const res = nextPageToken
      ? await api
          .asUser()
          .requestJira(
            route`/rest/api/3/search/jql?jql=${jql}&fields=${fields}&expand=${expand}&maxResults=${maxResults}&nextPageToken=${nextPageToken}`,
          )
      : await api
          .asUser()
          .requestJira(
            route`/rest/api/3/search/jql?jql=${jql}&fields=${fields}&expand=${expand}&maxResults=${maxResults}`,
          );
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Jira search failed: ${res.status} ${text.slice(0, 200)}`);
    }
    const page = (await res.json()) as JiraSearchResponse;
    const issues = page.issues ?? [];
    collected.push(...issues);
    pages += 1;

    if (page.isLast || !page.nextPageToken || issues.length === 0) break;
    nextPageToken = page.nextPageToken;
    if (pages > MAX_ISSUES / PAGE_SIZE + 2) break; // safety
  }

  // Trim oversized fields we don't need on the backend (keeps the POST small).
  const slim = collected.map((issue) => ({
    id: issue.id,
    key: issue.key,
    fields: issue.fields,
    changelog: issue.changelog,
  }));

  // Diagnostics: count how many issues actually carried a Sprint custom field.
  // Surfaced in the sync response so the user can see whether sprint
  // population is working before investigating the chart picker.
  const sprintFieldCandidates = [
    "customfield_10020",
    "customfield_10010",
    "customfield_10000",
    "customfield_10001",
    ...(discoveredSprintField ? [discoveredSprintField] : []),
  ];
  let issuesWithSprintField = 0;
  let firstSprintFieldHit: string | null = null;
  for (const issue of slim) {
    for (const fid of sprintFieldCandidates) {
      const v = (issue.fields as Record<string, unknown>)[fid];
      if (Array.isArray(v) && v.length > 0) {
        issuesWithSprintField += 1;
        firstSprintFieldHit = firstSprintFieldHit ?? fid;
        break;
      }
    }
  }

  // invokeRemote (server-side equivalent of @forge/bridge requestRemote).
  // Routes through the Forge runtime, which attaches a Forge Invocation
  // Token to the call so the backend's middleware authenticates it.
  // api.fetch by contrast does NOT attach a FIT — using it here landed us
  // a 401 "Missing FIT".
  const ingestRes = await invokeRemote(BACKEND_REMOTE_KEY, {
    path: "/api/forge/sync/ingest",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ payloads: slim }),
  });
  if (!ingestRes.ok) {
    const text = await ingestRes.text();
    throw new Error(`Backend ingest failed: ${ingestRes.status} ${text.slice(0, 200)}`);
  }
  const summary = (await ingestRes.json()) as Record<string, unknown>;
  return {
    fetched: slim.length,
    ...summary,
    // Sprint diagnostics — see if Sprint field is populated on the issues
    // we fetched. If `issuesWithSprintField` is 0 but the project actually
    // uses sprints, the Sprint field ID isn't in our candidate list and
    // we need to extend it (settings UI will surface manual override).
    sprintFieldDiscovered: discoveredSprintField,
    sprintFieldHit: firstSprintFieldHit,
    issuesWithSprintField,
  };
});

// ADR-0033: Settings-tab "Start historical backfill" button
// invokes `startBackfill` which (a) flips backend status to running
// (idempotent — re-clicks during a run are no-ops) and (b) pushes a
// new task onto the backfill queue. The consumer in
// `forge-prod/src/resolvers/backfill.ts` picks it up and runs in the
// background; the customer doesn't need to keep the tab open.
//
// This replaces the previous `runBackfillBatch` resolver from the
// ADR-0032 browser-loop era — deleted in the queue-rebuild commit.
// File-level customer-facing UX outcomes documented in ADR-0033
// "Locked product outcomes" section.
resolver.define("startBackfill", async () => {
  await invokeRemote(BACKEND_REMOTE_KEY, {
    path: "/api/forge/sync/backfill/start",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  await enqueueBackfill();
  return { enqueued: true };
});

// ADR-0046 — Settings-tab "Backfill historical status IDs" button.
// One-shot, synchronous (~1s for ~50k slices); the resolver fetches the
// current Jira status list via api.asApp().requestJira on /rest/api/3/status
// and forwards the array to the backend. The backend builds the name → id
// lookup and updates every legacy NULL status_id row whose name matches.
// Returns {updated_transitions, updated_slices, unresolved_names}.
//
// `api.asApp()` is the right authentication shape here: the backfill
// affects ALL of the tenant's data (not user-scoped), and the
// /rest/api/3/status endpoint is install-level info. `asUser()` would
// also work but tie the call to whichever user clicked the button.
resolver.define("backfillStatusIds", async () => {
  const statusesRes = await api
    .asApp()
    .requestJira(route`/rest/api/3/status`);
  if (!statusesRes.ok) {
    throw new Error(
      `Jira /rest/api/3/status returned ${statusesRes.status}; ` +
        `cannot run backfill without the current status list`,
    );
  }
  const statuses = (await statusesRes.json()) as Array<{
    id: string;
    name: string;
  }>;
  const backendRes = await invokeRemote(BACKEND_REMOTE_KEY, {
    path: "/api/forge/backfill/status-ids",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      statuses: statuses.map((s) => ({ id: String(s.id), name: s.name })),
    }),
  });
  return (await backendRes.json()) as {
    updated_transitions: number;
    updated_slices: number;
    unresolved_names: string[];
  };
});

export const dashboardResolver = resolver.getDefinitions();
