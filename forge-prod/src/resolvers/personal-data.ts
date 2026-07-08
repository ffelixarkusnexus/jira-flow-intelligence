// Personal Data Reporting — weekly scheduled trigger.
//
// Marketplace compliance hook per Atlassian's user-privacy guide:
//   https://developer.atlassian.com/platform/forge/user-privacy-guidelines/
//
// Atlassian requires every Marketplace app that stores personal data
// to periodically report the accountIds it holds and act on
// Atlassian's anonymization decisions. The protocol is:
//
//   1. Collect every accountId we hold + the latest updatedAt per account.
//   2. POST batches of <= 90 accounts to
//      https://api.atlassian.com/app/report-accounts/ with
//      `report:personal-data` scope (api.asApp() handles auth).
//   3. Atlassian returns per-account `status` — "closed" means the
//      account was deleted/anonymized; "updated" means the user info
//      changed (we ignore — webhook + daily reconcile already refresh).
//   4. Erase data for "closed" accounts.
//
// Run cadence: weekly (every Sunday). The default cycle from
// Atlassian's docs is 7 days; running once a week comfortably stays
// within that cycle while keeping invocation count low.
//
// Per-tenant scoping: this resolver is invoked once per install via
// the scheduled trigger. The backend's /api/forge/personal-data/* endpoints
// are tenant-scoped via the FIT auth middleware, so no tenant_id needs
// to flow through the wire — invokeRemote attaches the FIT and the
// backend resolves the tenant from there.

import api, { invokeRemote, route } from "@forge/api";

const BACKEND_REMOTE_KEY = "backend";
const ATLASSIAN_REPORT_ENDPOINT = "https://api.atlassian.com/app/report-accounts/";

// Atlassian doc: max 90 accounts per request.
const ATLASSIAN_BATCH_SIZE = 90;

// Per-page accountIds we pull from the backend per HTTP round-trip.
// Larger pages = fewer round-trips but larger payloads. 200 is well
// within both Forge's 25s invocation cap and Atlassian's request size
// limits at the report endpoint.
const BACKEND_PAGE_SIZE = 200;

interface AccountEntry {
  account_id: string;
  updated_at: string; // RFC 3339
}

interface AccountsResponse {
  accounts: AccountEntry[];
  next_cursor: string | null;
}

interface AtlassianAccountStatus {
  accountId: string;
  status: "closed" | "updated" | string;
}

interface AtlassianReportResponse {
  accounts?: AtlassianAccountStatus[];
}

async function fetchAllAccounts(): Promise<AccountEntry[]> {
  const all: AccountEntry[] = [];
  let cursor: string | null = null;
  // Hard guard against an unbounded loop if the backend ever
  // mis-implements paging — abort after 100 pages (= 20k accounts at
  // current page size, well above any plausible single-tenant volume).
  for (let i = 0; i < 100; i++) {
    const path = cursor
      ? `/api/forge/personal-data/accounts?limit=${BACKEND_PAGE_SIZE}&cursor=${encodeURIComponent(cursor)}`
      : `/api/forge/personal-data/accounts?limit=${BACKEND_PAGE_SIZE}`;
    const res = await invokeRemote(BACKEND_REMOTE_KEY, {
      path,
      method: "GET",
      headers: { "Content-Type": "application/json" },
    });
    if (!res.ok) {
      throw new Error(`Backend accounts fetch failed: ${res.status}`);
    }
    const body = (await res.json()) as AccountsResponse;
    all.push(...body.accounts);
    if (!body.next_cursor) {
      return all;
    }
    cursor = body.next_cursor;
  }
  throw new Error("Backend accounts pagination did not terminate after 100 pages");
}

function chunk<T>(items: T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < items.length; i += size) {
    out.push(items.slice(i, i + size));
  }
  return out;
}

async function reportBatch(batch: AccountEntry[]): Promise<string[]> {
  // Returns the accountIds Atlassian flagged as "closed" — these need
  // to be erased on our side. "updated" entries are intentionally
  // ignored: webhooks + daily reconcile already refresh display names.
  const res = await api.fetch(ATLASSIAN_REPORT_ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      // Atlassian's expected shape:
      // { accounts: [{ accountId, updatedAt }, ...] }
      accounts: batch.map((a) => ({
        accountId: a.account_id,
        updatedAt: a.updated_at,
      })),
    }),
  });
  if (!res.ok) {
    throw new Error(`Atlassian report-accounts failed: ${res.status} ${await res.text()}`);
  }
  const body = (await res.json()) as AtlassianReportResponse;
  return (body.accounts ?? [])
    .filter((entry) => entry.status === "closed")
    .map((entry) => entry.accountId);
}

async function eraseAccounts(accountIds: string[]): Promise<void> {
  if (accountIds.length === 0) return;
  const res = await invokeRemote(BACKEND_REMOTE_KEY, {
    path: "/api/forge/personal-data/erase",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ account_ids: accountIds }),
  });
  if (!res.ok) {
    throw new Error(`Backend erase failed: ${res.status} ${await res.text()}`);
  }
  const body = (await res.json()) as { issues_updated: number };
  console.log(
    `Erased ${accountIds.length} accountId(s); ${body.issues_updated} issue rows updated.`,
  );
}

export const personalDataReportingResolver = async () => {
  const startedAt = Date.now();
  try {
    const accounts = await fetchAllAccounts();
    if (accounts.length === 0) {
      console.log("Personal data reporting: tenant has no accountIds; skipping.");
      return;
    }

    const batches = chunk(accounts, ATLASSIAN_BATCH_SIZE);
    const closedAccountIds: string[] = [];
    for (const batch of batches) {
      const closed = await reportBatch(batch);
      closedAccountIds.push(...closed);
    }

    await eraseAccounts(closedAccountIds);
    console.log(
      `Personal data reporting cycle complete: reported ${accounts.length} accountId(s) in ${batches.length} batch(es); erased ${closedAccountIds.length}; ${Date.now() - startedAt}ms.`,
    );
  } catch (e) {
    // Don't swallow; let the Forge runtime log + retry on next cycle.
    // Atlassian doesn't penalize a single missed cycle, but consecutive
    // misses risk the listing flagging non-compliance.
    console.error(`Personal data reporting cycle failed: ${(e as Error).message}`);
    throw e;
  }
};

// `route` is exported in this module purely to keep the import lint-clean
// for future endpoints that need typed Jira routes from this file.
export { route };
