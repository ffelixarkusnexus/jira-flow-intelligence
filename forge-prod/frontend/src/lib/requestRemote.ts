// Forge bridge wrapper. `requestRemote` from @forge/bridge sends the call
// through the Forge runtime, which attaches the Forge Invocation Token
// (FIT) so our backend can authenticate it. The remote key matches
// `remotes[].key` in manifest.yml.
//
// Replaces the Connect-era apiClient at frontend/lib/api.ts.

import { requestRemote } from "@forge/bridge";

import type {
  AlertDestination,
  AlertDestinationIn,
  AlertDestinationsResponse,
  AlertRule,
  AlertsResponse,
  CfdResponse,
  CycleScatterResponse,
  InsightResponse,
  MetricsResponse,
  RecomputeStatus,
  RuleDestinations,
  SprintsResponse,
  SyncState,
  TenantSettings,
  TenantSettingsIn,
  WipAgingResponse,
  WipLimit,
  WipLimitsResponse,
  WorkSchedule,
  WorkScheduleIn,
} from "./types";

const REMOTE_KEY = "backend";

async function call<T>(
  path: string,
  init?: { method?: string; body?: unknown },
): Promise<T> {
  const opts: { path: string; method: string; headers?: Record<string, string>; body?: string } = {
    path,
    method: init?.method ?? "GET",
  };
  if (init?.body !== undefined) {
    opts.headers = { "Content-Type": "application/json" };
    opts.body = JSON.stringify(init.body);
  }
  const res = await requestRemote(REMOTE_KEY, opts);
  if (!res.ok) {
    throw new Error(`Backend ${path} ${res.status}: ${await res.text()}`);
  }
  // 204 No Content has no body to parse.
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// `jira:projectPage` is by definition project-scoped, so projectKey is
// almost always set. We keep it optional so the same client can still be
// used from non-project surfaces (settings page, future global view).
function withProject(path: string, projectKey?: string): string {
  if (!projectKey) return path;
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}project_key=${encodeURIComponent(projectKey)}`;
}

// Window choices live in two flavors: rolling-day (`days=30`) and calendar-
// bucketed (`period=mtd`). Endpoints that support `period` natively
// (insights, metrics) accept either; chart endpoints that only know `days`
// (cfd, cycle-scatter) translate calendar choices to a days-equivalent
// from first-of-period to now.
export type WindowChoice =
  | { kind: "days"; days: number }
  | { kind: "period"; period: "mtd" | "qtd" }
  // Sprint-bucketed windows. `sprintId` set = a specific sprint;
  // `span > 1` widens to last N sprints (current vs prior N).
  | { kind: "sprint"; sprintId?: number; span?: number };

function windowQuery(win: WindowChoice): string {
  if (win.kind === "days") return `days=${win.days}`;
  if (win.kind === "period") return `period=${win.period}`;
  // sprint
  const parts: string[] = [];
  if (win.sprintId !== undefined) parts.push(`sprint_id=${win.sprintId}`);
  parts.push(`sprint_span=${win.span ?? 1}`);
  return parts.join("&");
}

// Like `windowQuery` but never emits `days=` — used by endpoints that
// always include a `days=` query param of their own (chart endpoints
// have hard validation bounds on it). Returns "" for rolling-day windows
// since `days=` already covers that case.
function windowExtraQuery(win: WindowChoice): string {
  if (win.kind === "days") return "";
  return windowQuery(win);
}

function windowToDays(win: WindowChoice): number {
  if (win.kind === "days") return win.days;
  if (win.kind === "period") {
    const now = new Date();
    if (win.period === "mtd") {
      const first = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1));
      return Math.max(7, Math.ceil((now.getTime() - first.getTime()) / 86_400_000));
    }
    // QTD
    const qStartMonth = Math.floor(now.getUTCMonth() / 3) * 3;
    const first = new Date(Date.UTC(now.getUTCFullYear(), qStartMonth, 1));
    return Math.max(7, Math.ceil((now.getTime() - first.getTime()) / 86_400_000));
  }
  // Sprint windows fall back to a sensible chart span — the chart endpoints
  // accept `days` only and don't have sprint awareness yet. 30d covers a
  // typical 2-week sprint comfortably.
  return 30;
}

export const api = {
  getInsights: (
    win: WindowChoice = { kind: "days", days: 30 },
    projectKey?: string,
  ): Promise<InsightResponse> =>
    call(withProject(`/api/insights?${windowQuery(win)}&explain=true`, projectKey)),
  getMetrics: (
    win: WindowChoice = { kind: "days", days: 30 },
    projectKey?: string,
  ): Promise<MetricsResponse> =>
    call(withProject(`/api/metrics?${windowQuery(win)}`, projectKey)),
  getAlerts: (projectKey?: string): Promise<AlertsResponse> =>
    call(withProject("/api/alerts?limit=50", projectKey)),
  getWipAging: (projectKey?: string): Promise<WipAgingResponse> =>
    call(withProject("/api/insights/wip-aging", projectKey)),
  // Both chart endpoints honor `period` / `sprint_id` natively.
  // `days=` is sent as a fallback for the rolling-day case and so
  // the backend validators don't reject the request. Sprint/calendar
  // windows on the backend take precedence over `days` when set.
  getCfd: (win: WindowChoice, projectKey?: string): Promise<CfdResponse> => {
    const extra = windowExtraQuery(win);
    const path = `/api/insights/cfd?days=${windowToDays(win)}${extra ? `&${extra}` : ""}`;
    return call(withProject(path, projectKey));
  },
  getCycleScatter: (
    win: WindowChoice,
    projectKey?: string,
  ): Promise<CycleScatterResponse> => {
    const extra = windowExtraQuery(win);
    const path = `/api/insights/cycle-scatter?days=${windowToDays(win)}${extra ? `&${extra}` : ""}`;
    return call(withProject(path, projectKey));
  },
  getWipLimits: (projectKey?: string): Promise<WipLimitsResponse> =>
    call(withProject("/api/settings/wip-limits", projectKey)),
  putWipLimit: (limit: WipLimit): Promise<WipLimit> =>
    call("/api/settings/wip-limits", { method: "PUT", body: limit }),
  deleteWipLimit: (status: string, projectKey: string | null): Promise<void> => {
    const params = new URLSearchParams({ status });
    if (projectKey) params.set("project_key", projectKey);
    return call(`/api/settings/wip-limits?${params.toString()}`, { method: "DELETE" });
  },
  getSprints: (projectKey?: string): Promise<SprintsResponse> =>
    call(withProject("/api/sprints", projectKey)),
  // Feature 4 (TMT-gap): CSV download. Returns the raw CSV text + the
  // server-supplied filename, so the caller can build a Blob URL and trigger
  // a browser download. The Forge bridge surfaces the response body as text;
  // we don't try to stream — file sizes here are bounded (one project's
  // slices within a 7/30/90d window).
  // ADR-0043: work-schedule + recompute API.
  getWorkSchedule: (): Promise<WorkSchedule | null> => call("/api/forge/schedule"),
  putWorkSchedule: (s: WorkScheduleIn): Promise<WorkSchedule> =>
    call("/api/forge/schedule/activate", { method: "POST", body: s }),
  getRecomputeStatus: (): Promise<RecomputeStatus> => call("/api/forge/schedule/status"),
  exportCsv: async (
    projectKey: string,
    days: number,
  ): Promise<{ filename: string; body: string }> => {
    const path = `/api/export/csv?project=${encodeURIComponent(projectKey)}&days=${days}`;
    const res = await requestRemote(REMOTE_KEY, { path, method: "GET" });
    if (!res.ok) {
      throw new Error(`Backend ${path} ${res.status}: ${await res.text()}`);
    }
    const body = await res.text();
    // `Content-Disposition: attachment; filename="..."` — extract for the
    // download anchor. Falls back to a generic name if the header is absent.
    const cd = res.headers.get("Content-Disposition") || "";
    const m = /filename="([^"]+)"/.exec(cd);
    const filename = m?.[1] ?? `flow-intelligence-${projectKey}-${days}d.csv`;
    return { filename, body };
  },
  getSyncState: (): Promise<SyncState> => call("/api/forge/sync/state"),
  // ADR-0033 backfill control. Note that `startBackfill` is intentionally
  // NOT here — that's a Forge resolver invoke (Custom UI → @forge/bridge
  // invoke("startBackfill")) because it needs to push to the in-Forge
  // event queue. Only the side-effects that go through the backend
  // (acknowledge, set admin email) belong on this HTTP-client surface.
  acknowledgeBackfill: (): Promise<{ acknowledgedAt: string }> =>
    call("/api/forge/sync/backfill/acknowledge", { method: "POST" }),
  setAdminEmail: (email: string | null): Promise<{ adminContactEmail: string | null }> =>
    call("/api/forge/sync/admin-email", { method: "PUT", body: { email } }),
  // Dev-only seed endpoint, gated by ALLOW_DEMO_SEED on the backend
  // (per EnvConfig.allow_demo_seed). Used by the DemoSeedPanel in SettingsTab
  // to populate a freshly-installed tenant with 250 synthetic issues,
  // 5 sprints, and a designed Review-stage bottleneck — the same dataset the
  // Marketplace listing screenshots were captured against. FIT-auth-bound:
  // a caller can only seed their own tenant.
  seedDemo: (
    projectKey: string,
  ): Promise<{ issues: number; transitions: number; slices: number }> =>
    call(`/api/dev/seed-demo?project_key=${encodeURIComponent(projectKey)}`, {
      method: "POST",
    }),
  // Alert rules CRUD.
  getAlertRules: (): Promise<AlertRule[]> => call("/api/alerts/rules"),
  putAlertRule: (rule: AlertRule): Promise<AlertRule> =>
    call("/api/alerts/rules", { method: "PUT", body: rule }),
  deleteAlertRule: (ruleId: string): Promise<void> =>
    call(`/api/alerts/rules/${encodeURIComponent(ruleId)}`, { method: "DELETE" }),
  getTenantSettings: (): Promise<TenantSettings> => call("/api/settings/tenant"),
  putTenantSettings: (body: TenantSettingsIn): Promise<TenantSettings> =>
    call("/api/settings/tenant", { method: "PUT", body }),
  // ADR-0037 alert delivery destinations.
  getAlertDestinations: (): Promise<AlertDestinationsResponse> =>
    call("/api/alerts/destinations"),
  putAlertDestination: (body: AlertDestinationIn): Promise<AlertDestination> =>
    call("/api/alerts/destinations", { method: "PUT", body }),
  deleteAlertDestination: (id: string): Promise<void> =>
    call(`/api/alerts/destinations/${encodeURIComponent(id)}`, { method: "DELETE" }),
  testAlertDestination: (id: string): Promise<AlertDestination> =>
    call(`/api/alerts/destinations/${encodeURIComponent(id)}/test`, { method: "POST" }),
  getRuleDestinations: (ruleId: string): Promise<RuleDestinations> =>
    call(`/api/alerts/rules/${encodeURIComponent(ruleId)}/destinations`),
  putRuleDestinations: (ruleId: string, body: RuleDestinations): Promise<RuleDestinations> =>
    call(`/api/alerts/rules/${encodeURIComponent(ruleId)}/destinations`, {
      method: "PUT",
      body,
    }),
};
