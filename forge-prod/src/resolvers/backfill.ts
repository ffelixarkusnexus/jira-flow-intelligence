// Backfill consumer (ADR-0033).
//
// Lineage: this file was first shipped 2026-05-06 per ADR-0025
// (Forge consumer + queue), removed by the 285a240 pivot when
// queue.push() 400s on an existing install couldn't be resolved
// (ADR-0032 documents the bug cascade), and restored 2026-05-26 per
// ADR-0033 with current Forge primitive maturity + the trigger-handler-
// shape lesson from a494f8f baked in throughout the manifest chain.
//
// Why the queue path: the browser-loop ADR-0032 documented has UX
// limits (tab-resident, manual resume on close, no auto-start) that
// don't hold up for unattended installs. The queue path delivers all
// five locked outcomes from ADR-0033 the browser-loop couldn't.
//
// Why it works this time (corrected 2026-05-26 — the 2026-05-06
// hypothesis was wrong; real root cause now understood):
//   1. Trigger handlers (install lifecycle, webhooks, reconcile) are
//      plain async exports, NOT Resolver-wrapped — the a494f8f lesson.
//      Resolver wrapping is correct for UI invokes only.
//   2. Queue consumer handlers in @forge/events v2.x are ALSO plain
//      async exports — NOT Resolver-wrapped. The 2026-05-06 bug
//      cascade (queue.push() 400s) was caused by the consumer being
//      a Resolver with `.define("backfill", ...)` against the v2 API
//      which expects a direct function reference. The ADR-0032 pivot
//      attributed this to "install never re-accepted the new consumer
//      module" — that hypothesis was a red herring. Today's fresh dev
//      install consented to the consumer block at install time, and
//      Queue.push() STILL returned 400 until we fixed the consumer to
//      the v2 plain-async shape.
//
// Forge consumer functions now get a 15-minute invocation budget
// (configurable up to 900s in manifest's `function:` entry —
// up from the 10-min default ADR-0025 cited). We process up to
// MAX_PAGES_PER_INVOCATION pages per invocation, re-enqueue with the
// continuation token if more remain.
//
// Two producers push onto the queue:
//
//   1. `avi:forge:installed:app` lifecycle event — auto-triggers a
//      backfill when a new customer installs the app. Caught by
//      `lifecycle.ts` → `installLifecycleResolver` (plain async, NOT
//      Resolver-wrapped per the a494f8f lesson).
//   2. Manual UI button (Settings tab) — calls `startBackfill` resolver
//      via `@forge/bridge.invoke`. Used when a tenant admin wants to
//      re-run backfill after a failure or after extending the 50k cap.
//      Caught by the dashboard resolver's `startBackfill` definition.
//
// The consumer is idempotent within a chain: it always advances against
// `nextPageToken` from the previous batch's result, so re-pushes don't
// re-process completed batches.
//
// The progress endpoint on the backend (/api/forge/sync/backfill/progress)
// is where the proactive-notification side-effects live: terminal-state
// detection there fires the SES completion / failure / cap-reached emails
// per CLAUDE.md rule #9. The consumer just reports progress; the backend
// decides which email (if any) to send based on the state transition.

import api, { invokeRemote, route } from "@forge/api";
import type { AsyncEvent } from "@forge/events";
import { Queue } from "@forge/events";

// Note: NO Resolver import for the consumer. The @forge/events v2.x API
// expects the consumer handler to be a PLAIN ASYNC FUNCTION receiving
// `(event: AsyncEvent<T>, context)`, NOT a Resolver-wrapped handler with
// `.define("methodName", ...)`. The v1 Resolver-with-method pattern that
// shipped on 2026-05-06 (commit 1332d88) caused the queue.push() 400s
// in the bug cascade documented by ADR-0032 — the cascade was misdiagnosed
// at the time as a "consumer module re-consent" platform issue. Verified
// 2026-05-26 against current Atlassian docs + the @forge/events v2.1.4
// type definitions (`AsyncEvent<T> extends PushEvent<T>` with body at
// `event.body`).

const BACKEND_REMOTE_KEY = "backend";
const PAGE_SIZE = 100;
// Budget guard: Forge consumers now get up to 15 minutes via
// `timeoutSeconds: 900` on the function module (manifest.yml). We
// process up to MAX_PAGES_PER_INVOCATION pages per invocation, then
// re-enqueue with the continuation token if more remain. The 37 cap
// keeps a buffer well below the hard timeout — ingest+Jira per page
// is ~5-10s; 37 × 10s = 6.2min worst case. At p50 (~3s/page) we hit
// 37 pages in ~2 min and re-enqueue with plenty of platform headroom.
const MAX_PAGES_PER_INVOCATION = 37;
// Hard ceiling per backfill run. Sites with > 50k tickets need a
// per-tenant override — surfaced via the 50k-cap email (customer-copy
// §3) which directs the admin to email support@example.com.
const MAX_TOTAL_ISSUES = 50_000;

// Static fields always fetched. The trailing customfield_* IDs are the
// common Jira Software story-points + sprint candidates; backend extracts
// whichever is populated. Dashboard's syncJira resolver also calls
// `discoverSprintFieldId()` to dynamically pick up sites whose sprint
// field uses a non-standard ID — the backfill consumer does the same
// thing via discoverSprintFieldAsApp() below. Without that discovery
// step the consumer misses sprint membership on sites whose sprint
// field is outside this static list, which manifests in the UI as the
// window picker missing the Current sprint / Last sprint / Last 3
// sprints options (Sprint rows never get created in backend).
const STATIC_FIELDS = [
  "summary",
  "status",
  "issuetype",
  "created",
  "updated",
  "resolutiondate",
  "project",
  "assignee",
  "priority",
  // Story-points candidates.
  "customfield_10016",
  "customfield_10026",
  "customfield_10002",
  "customfield_10004",
  // Sprint custom-field candidates (common Cloud defaults).
  "customfield_10020",
  "customfield_10010",
  "customfield_10000",
  "customfield_10001",
  "customfield_10007",
];

// Mirror of `discoverSprintFieldId` from dashboard.ts but using
// api.asApp() because consumer functions run server-side with no user
// session — `asUser()` throws AUTH_TYPE_UNAVAILABLE. Returns the
// custom-field ID whose Jira schema marks it as the Sprint field, or
// null if no match / on error. Called once per consumer invocation;
// the /rest/api/3/field endpoint is fast (~200 fields, no pagination).
async function discoverSprintFieldAsApp(): Promise<string | null> {
  try {
    const res = await api.asApp().requestJira(route`/rest/api/3/field`);
    if (!res.ok) return null;
    const fields = (await res.json()) as Array<{
      id: string;
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

interface JiraIssue {
  id: string;
  key: string;
  fields: Record<string, unknown>;
  changelog?: { histories?: unknown[] };
}

interface BackfillTask {
  [key: string]: unknown;
  // Token returned by Jira's previous page. Optional/missing on the
  // first push — Forge's queue platform rejected null-valued JSON
  // fields with a 400 + empty response body in 2026-05-06 debugging,
  // so we omit absent keys rather than serializing `null`.
  nextPageToken?: string;
  alreadyProcessed: number;
  isFirstBatch: boolean;
}

const backfillQueue = new Queue({ key: "backfillqueue" });

// ----- Producer: kick off a new backfill ----------------------------------

export async function enqueueBackfill(): Promise<void> {
  const task: BackfillTask = {
    alreadyProcessed: 0,
    isFirstBatch: true,
  };
  await backfillQueue.push({ body: task });
}

// ----- Consumer ------------------------------------------------------------
//
// @forge/events v2.x consumer handler — plain async function. Receives an
// AsyncEvent<BackfillTask> with the pushed task at `event.body`. The
// manifest's `consumer.[].function: backfillConsumer` references this
// export by name (no method dispatch).

export async function backfillConsumer(event: AsyncEvent<BackfillTask>): Promise<void> {
  const task: BackfillTask = event.body;
  let nextPageToken: string | undefined = task.nextPageToken;
  let alreadyProcessed = task.alreadyProcessed ?? 0;

  // Mark `running` on the first batch even if the producer didn't (e.g.
  // the install-event path goes straight to push without calling start).
  if (task.isFirstBatch) {
    await invokeRemote(BACKEND_REMOTE_KEY, {
      path: "/api/forge/sync/backfill/start",
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
  }

  // Build the fields list dynamically — static fallback chain PLUS the
  // sprint custom-field ID discovered via /rest/api/3/field. Without this
  // discovery, sites whose Sprint field is at a non-default custom-field
  // ID never get sprint membership ingested, which manifests as the
  // window picker missing the three sprint options. Matching what the
  // dashboard's syncJira resolver already does.
  const discoveredSprintField = await discoverSprintFieldAsApp();
  const fieldList = [...STATIC_FIELDS];
  if (discoveredSprintField && !fieldList.includes(discoveredSprintField)) {
    fieldList.push(discoveredSprintField);
  }
  const FIELDS_FOR_INGEST = fieldList.join(",");

  for (let page = 0; page < MAX_PAGES_PER_INVOCATION; page += 1) {
    if (alreadyProcessed >= MAX_TOTAL_ISSUES) {
      await reportProgress({
        processed_delta: 0,
        next_page_token: null,
        done: true,
      });
      console.log(
        `Backfill: cap reached at ${alreadyProcessed} issues; marking done`,
      );
      return;
    }

    // Older-first ordering so the dashboard fills with historical context
    // before the most-recent activity. For very large sites the consumer
    // re-enqueues mid-pull anyway, so order is mostly cosmetic.
    //
    // api.asApp() (NOT asUser): consumer functions run server-side with
    // no user session — same constraint as trigger handlers documented in
    // commit a494f8f. asUser() throws AUTH_TYPE_UNAVAILABLE here. asApp()
    // uses the granular OAuth 2.0 scopes declared in manifest.yml
    // (read:issue:jira, read:issue.changelog:jira, read:jql:jira,
    // read:project:jira, read:user:jira) which are already in place
    // exactly for this case. Verified 2026-05-26 against the original
    // 2026-05-06 cascade — this was the THIRD root cause hidden behind
    // the queue.push() 400, only observable once the consumer actually
    // ran and tried to call Jira.
    // `created >= "1970-01-01"` is required — Jira's /search/jql endpoint
    // refuses unbounded JQL with "Unbounded JQL queries are not allowed
    // here." Unix epoch pre-dates any real Jira instance, so functionally
    // pulls every issue. Same pattern as the browser-loop dashboard
    // resolver had pre-pivot (commit 5550cde). Without this, Jira
    // returns 400 with the unbounded-JQL message — the try/catch above
    // reports it as `Backfill: Jira search failed` and the backend
    // flips status to failed.
    const jql = 'created >= "1970-01-01" ORDER BY created ASC';
    // Wrap requestJira in try/catch — `api.asApp().requestJira(...)` THROWS
    // on auth failures (e.g. AUTH_TYPE_UNAVAILABLE) and on platform proxy
    // errors, instead of returning !res.ok. Without this catch the
    // consumer crashes mid-invocation and never reports the error back to
    // the backend, leaving tenant.backfill_status stuck at "running"
    // forever. Catch + reportProgress with error guarantees the
    // state machine always converges to a terminal status the customer
    // can act on (Retry button via the failure email + Settings UI).
    let res: Awaited<ReturnType<ReturnType<typeof api.asApp>["requestJira"]>>;
    try {
      res = nextPageToken
        ? await api
            .asApp()
            .requestJira(
              route`/rest/api/3/search/jql?jql=${jql}&fields=${FIELDS_FOR_INGEST}&expand=${"changelog"}&maxResults=${String(PAGE_SIZE)}&nextPageToken=${nextPageToken}`,
            )
        : await api
            .asApp()
            .requestJira(
              route`/rest/api/3/search/jql?jql=${jql}&fields=${FIELDS_FOR_INGEST}&expand=${"changelog"}&maxResults=${String(PAGE_SIZE)}`,
            );
    } catch (thrown) {
      const errAny = thrown as { status?: number; errorCode?: string; message?: string };
      const msg = `Jira request threw: ${errAny.errorCode ?? errAny.status ?? ""} ${
        errAny.message ?? String(thrown)
      }`.slice(0, 400);
      console.error(`Backfill: ${msg}`);
      await reportProgress({
        processed_delta: 0,
        next_page_token: nextPageToken ?? null,
        error: msg,
      });
      return;
    }
    if (!res.ok) {
      const text = await res.text();
      const msg = `Jira search failed: ${res.status} ${text.slice(0, 200)}`;
      console.error(`Backfill: ${msg}`);
      await reportProgress({
        processed_delta: 0,
        next_page_token: nextPageToken ?? null,
        error: msg,
      });
      return;
    }
    const pageJson = (await res.json()) as {
      issues?: JiraIssue[];
      isLast?: boolean;
      nextPageToken?: string;
    };
    const issues = pageJson.issues ?? [];

    if (issues.length > 0) {
      const slim = issues.map((i) => ({
        id: i.id,
        key: i.key,
        fields: i.fields,
        changelog: i.changelog,
      }));
      const ingestRes = await invokeRemote(BACKEND_REMOTE_KEY, {
        path: "/api/forge/sync/ingest",
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ payloads: slim, skip_if_stale: true }),
      });
      if (!ingestRes.ok) {
        const text = await ingestRes.text();
        const msg = `Backfill ingest failed: ${ingestRes.status} ${text.slice(0, 200)}`;
        console.error(msg);
        await reportProgress({
          processed_delta: 0,
          next_page_token: nextPageToken ?? null,
          error: msg,
        });
        return;
      }
      alreadyProcessed += issues.length;
      await reportProgress({
        processed_delta: issues.length,
        next_page_token: pageJson.nextPageToken ?? null,
        done: false,
      });
    }

    if (pageJson.isLast || !pageJson.nextPageToken || issues.length === 0) {
      await reportProgress({
        processed_delta: 0,
        next_page_token: null,
        done: true,
      });
      console.log(`Backfill: finished, total processed = ${alreadyProcessed}`);
      return;
    }
    nextPageToken = pageJson.nextPageToken;
  }

  // Hit the per-invocation page cap with more pages remaining — re-enqueue
  // so the consumer picks up where we left off. Forge invokes the consumer
  // again with our pushed payload after the queue has slack. Build the
  // continuation object property-by-property so `nextPageToken` is omitted
  // entirely when undefined (Forge queue rejects null-valued JSON fields).
  const continuation: BackfillTask = {
    alreadyProcessed,
    isFirstBatch: false,
  };
  if (nextPageToken !== undefined) {
    continuation.nextPageToken = nextPageToken;
  }
  await backfillQueue.push({ body: continuation });
  console.log(
    `Backfill: invocation paused at ${alreadyProcessed} issues, re-enqueued`,
  );
}

interface ProgressBody {
  processed_delta: number;
  next_page_token: string | null;
  done?: boolean;
  error?: string;
}

async function reportProgress(body: ProgressBody): Promise<void> {
  const res = await invokeRemote(BACKEND_REMOTE_KEY, {
    path: "/api/forge/sync/backfill/progress",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text();
    console.error(`Backfill progress report failed: ${res.status} ${text.slice(0, 200)}`);
  }
}

// (v2.x: no Resolver.getDefinitions() needed — backfillConsumer is the
// direct export the manifest binds via `function: backfillConsumer`.)
