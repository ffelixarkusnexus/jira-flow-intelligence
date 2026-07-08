import { useEffect, useState } from "react";
import { invoke } from "@forge/bridge";

import { AlertsList } from "./components/AlertsList";
import { BottleneckPanel } from "./components/BottleneckPanel";
import { CfdChart } from "./components/CfdChart";
import { CycleScatterChart } from "./components/CycleScatterChart";
import { InsightCard } from "./components/InsightCard";
import { SettingsTab } from "./components/SettingsTab";
import { TrendsList } from "./components/TrendsList";
import { WipAgingChart } from "./components/WipAgingChart";
import { RecomputeBanner } from "./components/WorkSchedulePanel";
import { api } from "./lib/requestRemote";
import type {
  AlertsResponse,
  CfdResponse,
  CycleScatterResponse,
  InsightResponse,
  MetricsResponse,
  SprintOut,
  SyncState,
  WipAgingResponse,
} from "./lib/types";

interface DashboardContext {
  cloudId: string;
  installationId: string;
  projectKey?: string;
  siteUrl?: string;
  // Forge environment: "development" | "staging" | "production" (lower-
  // cased). Drives UI gates for dev-only panels (DemoSeedPanel etc.) so
  // customers on the Marketplace production env never see them.
  environmentType?: string | null;
}

interface DashboardData {
  insights: InsightResponse;
  metrics: MetricsResponse;
  alerts: AlertsResponse;
}

type Tab = "overview" | "flow" | "settings";

// `WindowChoice` carries either a rolling-day window (7/30/90) or a
// calendar-bucketed period (MTD/QTD). Calendar windows compute their own
// "previous full period" on the backend so sprint-style
// comparisons work correctly.
type WindowChoice =
  | { kind: "days"; days: number; label: string }
  | { kind: "period"; period: "mtd" | "qtd"; label: string }
  | {
      kind: "sprint";
      label: string;
      sprintId?: number;
      span?: number;
      // Display name of the sprint (or "Last 3 sprints") used for
      // sprint-over-sprint framing on the bottleneck card.
      sprintLabel?: string;
    };

const STATIC_WINDOWS: WindowChoice[] = [
  { kind: "days", days: 7, label: "7d" },
  { kind: "days", days: 30, label: "30d" },
  { kind: "days", days: 90, label: "90d" },
  { kind: "period", period: "mtd", label: "MTD" },
  { kind: "period", period: "qtd", label: "QTD" },
];

function isSameWindow(a: WindowChoice, b: WindowChoice): boolean {
  if (a.kind !== b.kind) return false;
  if (a.kind === "days" && b.kind === "days") return a.days === b.days;
  if (a.kind === "period" && b.kind === "period") return a.period === b.period;
  if (a.kind === "sprint" && b.kind === "sprint") {
    return a.sprintId === b.sprintId && (a.span ?? 1) === (b.span ?? 1);
  }
  return false;
}

// Build sprint-bucketed window options from the project's sprints. Picker
// shows: Current sprint (active sprint, no explicit id), Last sprint
// (most-recent closed), Last 3 sprints (span=3 starting from active or
// most-recent closed). Empty when the project has no sprints — picker
// hides the sprint section entirely on Kanban-only projects.
function sprintWindows(sprints: SprintOut[]): WindowChoice[] {
  if (sprints.length === 0) return [];
  const active = sprints.find((s) => s.state === "active");
  const closed = sprints.filter((s) => s.state === "closed");
  const out: WindowChoice[] = [];
  if (active) {
    out.push({
      kind: "sprint",
      label: "Current sprint",
      sprintId: active.id,
      span: 1,
      sprintLabel: active.name,
    });
  }
  if (closed.length > 0) {
    out.push({
      kind: "sprint",
      label: "Last sprint",
      sprintId: closed[0].id,
      span: 1,
      sprintLabel: closed[0].name,
    });
  }
  if (active || closed.length >= 3) {
    out.push({
      kind: "sprint",
      label: "Last 3 sprints",
      span: 3,
      sprintLabel: "last 3 sprints",
    });
  }
  return out;
}

async function loadDashboard(
  win: WindowChoice,
  projectKey?: string,
): Promise<DashboardData> {
  const [insights, metrics, alerts] = await Promise.all([
    api.getInsights(win, projectKey),
    api.getMetrics(win, projectKey),
    api.getAlerts(projectKey),
  ]);
  return { insights, metrics, alerts };
}

// "Sparse" = the chosen window has no completions in any status. Distinguishes
// "actually healthy flow" from "low-activity project that the bottleneck
// pipeline can't reason about." Used to swap the InsightCard empty-state copy.
function isWindowSparse(data: DashboardData | null): boolean {
  if (!data) return false;
  const cur = data.metrics.current;
  if (cur.cycle_time_count > 0) return false;
  return cur.statuses.every((s) => s.sample_size === 0);
}

// "5 minutes ago" / "2 hours ago" / "3 days ago" — coarse buckets are
// what admins actually want here: sub-minute precision is noise, and the
// purpose is to confirm webhooks are alive, not to time them.
function relativeTime(iso: string | null): string {
  if (!iso) return "never synced";
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms) || ms < 0) return "just now";
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return "just now";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  return `${day}d ago`;
}

function hostnameFromSiteUrl(url: string | undefined): string | undefined {
  if (!url) return undefined;
  try {
    return new URL(url).hostname;
  } catch {
    return undefined;
  }
}

export function App() {
  const [ctx, setCtx] = useState<DashboardContext | null>(null);
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  // Surface "last synced N min ago" so admins can confirm webhooks
  // are flowing. Updated after every successful manual sync; for webhook
  // / scheduled-reconcile updates, the backend stamps it on each ingest.
  // ADR-0033: the same /state endpoint also drives the dashboard
  // completion banner (rendered above Overview content when backfill
  // status is `completed` and acknowledgedAt is null) and tracks the
  // admin_contact_email for the SES notification destination.
  const [syncState, setSyncState] = useState<SyncState | null>(null);
  // Default 30d rolling: 7d hits sparseness on low-activity projects and
  // the Overview's bottleneck pipeline has nothing to reason from. 30d
  // trades a bit of recency for enough completion samples to score.
  // Users can switch to MTD/QTD calendar or sprint windows.
  const [win, setWin] = useState<WindowChoice>(STATIC_WINDOWS[1]);
  const [sprints, setSprints] = useState<SprintOut[]>([]);
  const [wipAging, setWipAging] = useState<WipAgingResponse | null>(null);
  const [wipError, setWipError] = useState<string | null>(null);
  const [cfd, setCfd] = useState<CfdResponse | null>(null);
  const [cfdError, setCfdError] = useState<string | null>(null);
  const [scatter, setScatter] = useState<CycleScatterResponse | null>(null);
  const [scatterError, setScatterError] = useState<string | null>(null);

  // Resolve Forge context once. The data fetch lives in the next effect so
  // changing `win` only refetches dashboard data, not the Forge context.
  useEffect(() => {
    let cancelled = false;
    invoke<DashboardContext>("getContext").then(
      (resolved) => !cancelled && setCtx(resolved),
      (e) => !cancelled && setError((e as Error).message),
    );
    return () => {
      cancelled = true;
    };
  }, []);

  // Pull the project's sprints once ctx is resolved. Sprints come from the
  // issue payload (Path 3) so this list reflects whatever syncJira has
  // already discovered. Empty list = picker hides the sprint section.
  useEffect(() => {
    if (!ctx) return;
    let cancelled = false;
    api.getSprints(ctx.projectKey).then(
      (r) => !cancelled && setSprints(r.sprints),
      // Sprint failure is non-fatal — picker just hides the sprint options.
      () => !cancelled && setSprints([]),
    );
    return () => {
      cancelled = true;
    };
  }, [ctx]);

  // Pull the tenant's sync state (last sync timestamp + backfill block +
  // admin_contact_email) once ctx is resolved. Used by the "Last synced
  // N min ago" indicator AND the ADR-0033 dashboard completion banner.
  // Also polls every 60s while mounted so a customer who left the
  // dashboard open sees the banner shortly after backfill completes.
  useEffect(() => {
    if (!ctx) return;
    let cancelled = false;
    const fetchSyncState = () => {
      api.getSyncState().then(
        (s) => !cancelled && setSyncState(s),
        () => {
          /* network blip — keep prior state, retry on next poll */
        },
      );
    };
    fetchSyncState();
    const id = window.setInterval(fetchSyncState, 60_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [ctx]);

  // Loads Overview data once ctx is resolved, and on every window change.
  useEffect(() => {
    if (!ctx) return;
    let cancelled = false;
    (async () => {
      try {
        const next = await loadDashboard(win, ctx.projectKey);
        if (!cancelled) setData(next);
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [win, ctx]);

  // CFD/Scatter follow the shared window. WIP Aging is in-flight only —
  // window doesn't apply, so we only refetch on project change. Charts
  // that already loaded for the current (win, project) tuple are gated
  // by the `=== null && error === null` checks below.
  useEffect(() => {
    if (tab !== "flow") return;
    let cancelled = false;
    const pk = ctx?.projectKey;

    if (wipAging === null && wipError === null) {
      api.getWipAging(pk).then(
        (v) => !cancelled && setWipAging(v),
        (e) => !cancelled && setWipError((e as Error).message),
      );
    }
    if (cfd === null && cfdError === null) {
      api.getCfd(win, pk).then(
        (v) => !cancelled && setCfd(v),
        (e) => !cancelled && setCfdError((e as Error).message),
      );
    }
    if (scatter === null && scatterError === null) {
      api.getCycleScatter(win, pk).then(
        (v) => !cancelled && setScatter(v),
        (e) => !cancelled && setScatterError((e as Error).message),
      );
    }

    return () => {
      cancelled = true;
    };
  }, [tab, win, ctx?.projectKey, wipAging, wipError, cfd, cfdError, scatter, scatterError]);

  // Invalidate the windowed Flow charts when window or projectKey changes
  // so the next Flow render refetches. WIP Aging is unaffected by window,
  // so only projectKey invalidates it.
  useEffect(() => {
    setCfd(null);
    setCfdError(null);
    setScatter(null);
    setScatterError(null);
  }, [win, ctx?.projectKey]);

  useEffect(() => {
    setWipAging(null);
    setWipError(null);
  }, [ctx?.projectKey]);

  if (error) {
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-900 dark:border-red-900/50 dark:bg-red-900/20 dark:text-red-100">
        Could not load dashboard: {error}
      </div>
    );
  }

  if (!data || !ctx) {
    return (
      <div className="rounded-xl border border-ink-200 bg-white p-6 text-ink-600 dark:border-ink-800 dark:bg-ink-900 dark:text-ink-400">
        Loading flow intelligence…
      </div>
    );
  }

  const hostname = hostnameFromSiteUrl(ctx.siteUrl);

  return (
    <main className="space-y-6 p-6">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-baseline gap-4">
          <p className="text-xs uppercase tracking-wide text-ink-400">
            cloud <code className="font-mono">{ctx.cloudId}</code>
            {ctx.projectKey ? <> · project <code className="font-mono">{ctx.projectKey}</code></> : null}
          </p>
          <nav className="flex items-center gap-1 text-xs">
            <TabButton active={tab === "overview"} onClick={() => setTab("overview")}>
              Overview
            </TabButton>
            <TabButton active={tab === "flow"} onClick={() => setTab("flow")}>
              Flow
            </TabButton>
            <TabButton active={tab === "settings"} onClick={() => setTab("settings")}>
              Settings
            </TabButton>
          </nav>
        </div>
        <div className="flex items-center gap-3">
          {tab !== "settings" && (
            <div
              className="flex items-center gap-1 rounded-md border border-ink-200 bg-white p-0.5 text-xs dark:border-ink-800 dark:bg-ink-900"
              role="group"
              aria-label="Window size"
              title={
                tab === "flow"
                  ? "Affects CFD and Cycle Scatter. WIP Aging is in-flight only and ignores the window."
                  : undefined
              }
            >
              {[...STATIC_WINDOWS, ...sprintWindows(sprints)].map((opt) => {
                const active = isSameWindow(opt, win);
                return (
                  <button
                    key={opt.label}
                    type="button"
                    onClick={() => setWin(opt)}
                    aria-pressed={active}
                    className={
                      active
                        ? "rounded-sm bg-ink-900 px-2.5 py-1 font-medium text-white dark:bg-white dark:text-ink-900"
                        : "rounded-sm px-2.5 py-1 text-ink-600 hover:bg-ink-100 dark:text-ink-400 dark:hover:bg-ink-800"
                    }
                  >
                    {opt.label}
                  </button>
                );
              })}
            </div>
          )}
          {tab !== "settings" && ctx?.projectKey && (
            <ExportCsvButton projectKey={ctx.projectKey} window={win} />
          )}
          <span
            className="text-xs text-ink-500 dark:text-ink-400"
            title="Webhooks keep this dashboard in sync automatically; daily reconciliation catches anything missed."
          >
            Last sync: {relativeTime(syncState?.lastSyncedAt ?? null)}
          </span>
        </div>
      </div>

      {tab === "overview" && (
        <>
          <RecomputeBanner />
          <BackfillCompletionBanner
            syncState={syncState}
            onDismiss={async () => {
              await api.acknowledgeBackfill();
              // Re-fetch so acknowledgedAt is non-null on next render.
              const next = await api.getSyncState();
              setSyncState(next);
            }}
          />
          <InsightCard
            bottleneck={data.insights.bottleneck}
            explanation={data.insights.explanation}
            isSparse={isWindowSparse(data)}
            windowLabel={win.label}
          />
          <BottleneckPanel
            bottleneck={data.insights.bottleneck}
            metrics={data.metrics}
            priorLabel={
              win.kind === "sprint" && win.sprintLabel
                ? `vs ${win.sprintLabel}`
                : undefined
            }
          />
          <AlertsList alerts={data.alerts.alerts} />
          <TrendsList trends={data.insights.trends} />
        </>
      )}

      {tab === "flow" && (
        <>
          {cfdError && <ChartErrorBox label="Cumulative flow" message={cfdError} />}
          {!cfdError && cfd === null && <ChartLoadingBox label="Cumulative flow" />}
          {cfd && <CfdChart data={cfd} windowLabel={win.label} />}

          {scatterError && (
            <ChartErrorBox label="Cycle time scatter" message={scatterError} />
          )}
          {!scatterError && scatter === null && (
            <ChartLoadingBox label="Cycle time scatter" />
          )}
          {scatter && (
            <CycleScatterChart
              data={scatter}
              cloudHostname={hostname}
              windowLabel={win.label}
            />
          )}

          {wipError && (
            <ChartErrorBox label="WIP aging" message={wipError} />
          )}
          {!wipError && wipAging === null && <ChartLoadingBox label="WIP aging" />}
          {wipAging && <WipAgingChart data={wipAging} cloudHostname={hostname} />}
        </>
      )}

      {tab === "settings" && (
        <SettingsTab
          projectKey={ctx.projectKey}
          knownStatuses={data.metrics.current.statuses.map((s) => s.status)}
          environmentType={ctx.environmentType ?? null}
        />
      )}
    </main>
  );
}

// Feature 4 (TMT-gap): Export CSV button. Lands next to the window picker in
// the dashboard top bar. Quiet styling — same height as the picker, neutral
// border, no accent color. Direct download, no modal / confirmation.
function ExportCsvButton({
  projectKey,
  window,
}: {
  projectKey: string;
  window: WindowChoice;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const onClick = async () => {
    setBusy(true);
    setError(null);
    try {
      // Use the window's `days` for rolling-day windows; for period/sprint
      // windows the backend currently only knows `days`, so fall back to
      // the same translation `windowToDays` uses on the chart endpoints.
      const days =
        window.kind === "days"
          ? window.days
          : window.kind === "period" && window.period === "mtd"
            ? Math.max(7, new Date().getUTCDate())
            : window.kind === "period" && window.period === "qtd"
              ? Math.max(
                  7,
                  Math.ceil(
                    (Date.now() -
                      Date.UTC(
                        new Date().getUTCFullYear(),
                        Math.floor(new Date().getUTCMonth() / 3) * 3,
                        1,
                      )) /
                      86_400_000,
                  ),
                )
              : 30;
      const { filename, body } = await api.exportCsv(projectKey, days);
      const blob = new Blob([body], { type: "text/csv;charset=utf-8" });
      const href = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = href;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(href);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={onClick}
        disabled={busy}
        title="Download a CSV of this project's issue + time-slice data for the current window. Includes the external-blocking marker (ADR-0042) so the file is traceable to attribution."
        className="rounded-md border border-ink-200 bg-white px-2.5 py-1 text-xs text-ink-700 hover:bg-ink-50 disabled:opacity-50 dark:border-ink-800 dark:bg-ink-900 dark:text-ink-200 dark:hover:bg-ink-800"
      >
        {busy ? "Exporting…" : "Export CSV"}
      </button>
      {error && (
        <span className="text-xs text-red-600 dark:text-red-400" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        active
          ? "rounded-md bg-ink-900 px-3 py-1 font-medium text-white dark:bg-white dark:text-ink-900"
          : "rounded-md px-3 py-1 text-ink-600 hover:bg-ink-100 dark:text-ink-400 dark:hover:bg-ink-800"
      }
    >
      {children}
    </button>
  );
}

// ADR-0033 dashboard completion banner (outcome #4 passive surface).
// Renders when backfill status is `completed` AND the customer hasn't
// dismissed it yet (`acknowledgedAt === null`). Two variants — one for
// normal completion and one for the 50,000-issue cap.
//
// The email (active push) is the primary channel; this banner is
// the secondary surface for customers who happen to be on the dashboard
// when backfill finishes, OR who never set an admin_contact_email and
// thus didn't receive the email.
const BACKFILL_CAP = 50_000;

function BackfillCompletionBanner(props: {
  syncState: SyncState | null;
  onDismiss: () => Promise<void>;
}) {
  const { syncState, onDismiss } = props;
  const backfill = syncState?.backfill;
  if (!backfill) return null;
  if (backfill.status !== "completed") return null;
  if (backfill.acknowledgedAt) return null;

  const n = (backfill.processedIssues ?? 0).toLocaleString();
  const capReached = (backfill.processedIssues ?? 0) >= BACKFILL_CAP;

  return (
    <div
      role="status"
      className={
        capReached
          ? "flex items-center justify-between gap-4 rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-900/50 dark:bg-amber-900/20 dark:text-amber-100"
          : "flex items-center justify-between gap-4 rounded-xl border border-emerald-200 bg-emerald-50/60 px-4 py-3 text-sm text-emerald-900 dark:border-emerald-900/50 dark:bg-emerald-900/20 dark:text-emerald-100"
      }
    >
      <p>
        {capReached ? (
          <>
            Backfill complete at the 50,000-issue cap. Older history is not yet
            indexed —{" "}
            <a
              className="underline"
              href="mailto:support@example.com?subject=Backfill%20cap%20extension%20request"
            >
              email support to extend
            </a>
            .
          </>
        ) : (
          <>Historical backfill complete. {n} issues now indexed across your flow charts.</>
        )}
      </p>
      <button
        type="button"
        onClick={() => {
          void onDismiss();
        }}
        className="rounded-md border border-current/30 px-2 py-1 text-xs font-medium hover:bg-current/5"
      >
        Dismiss
      </button>
    </div>
  );
}

function ChartLoadingBox({ label }: { label: string }) {
  return (
    <div className="rounded-xl border border-ink-200 bg-white p-6 text-ink-600 dark:border-ink-800 dark:bg-ink-900 dark:text-ink-400">
      Loading {label}…
    </div>
  );
}

function ChartErrorBox({ label, message }: { label: string; message: string }) {
  return (
    <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-900 dark:border-red-900/50 dark:bg-red-900/20 dark:text-red-100">
      Could not load {label}: {message}
    </div>
  );
}
