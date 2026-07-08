// Settings tab.
//
// Two panels today:
// - WIP limits: per-status WIP caps that turn raw WIP averages
//   into actionable `current / limit` signals (ADR-0022). Saves per
//   row on blur — no global "save everything" round-trip.
// - Historical backfill: one-click pull of all the tenant's
//   Jira history into the dashboard. New installs trigger this auto-
//   matically; existing installs (those that installed before the
//   backfill feature) need the manual button. Status/progress comes
//   from /api/forge/sync/state.

import { invoke } from "@forge/bridge";
import { useCallback, useEffect, useState } from "react";

import { api } from "../lib/requestRemote";
import type { SyncState, WipLimit } from "../lib/types";
import { AlertDestinationsPanel } from "./AlertDestinationsPanel";
import { AlertRulesPanel } from "./AlertRulesPanel";
import { TenantSettingsPanel } from "./TenantSettingsPanel";
import { WorkSchedulePanel } from "./WorkSchedulePanel";

interface Props {
  projectKey: string | undefined;
  // Statuses we know about for the active project — sourced from Overview's
  // metrics response so users only configure limits for stages that exist.
  knownStatuses: string[];
  // Forge environment the caller's install is on. The DemoSeedPanel only
  // renders when this is "development" — customers on production-env
  // installs must never see it (clicking would wipe their tenant data
  // with synthetic fixtures, which they could mistake for their actual
  // Jira board being corrupted → panic uninstall).
  environmentType: string | null;
}

interface Draft {
  status: string;
  max_in_progress: string;
  breach_minutes: string;
}

const DRAFT_EMPTY: Draft = { status: "", max_in_progress: "3", breach_minutes: "0" };

export function SettingsTab({ projectKey, knownStatuses, environmentType }: Props) {
  const [limits, setLimits] = useState<WipLimit[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft>(DRAFT_EMPTY);

  const refetch = () => {
    api.getWipLimits(projectKey).then(
      (r) => setLimits(r.limits),
      (e) => setError((e as Error).message),
    );
  };

  useEffect(refetch, [projectKey]);

  const onAdd = async () => {
    if (!draft.status.trim()) return;
    const max = Number.parseInt(draft.max_in_progress, 10);
    const breach = Number.parseInt(draft.breach_minutes, 10);
    if (Number.isNaN(max) || max < 0) {
      setError("max_in_progress must be a non-negative integer");
      return;
    }
    if (Number.isNaN(breach) || breach < 0) {
      setError("breach_minutes must be a non-negative integer");
      return;
    }
    setSaving(draft.status);
    setError(null);
    try {
      await api.putWipLimit({
        project_key: projectKey ?? null,
        status: draft.status.trim(),
        max_in_progress: max,
        breach_minutes: breach,
      });
      setDraft(DRAFT_EMPTY);
      refetch();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(null);
    }
  };

  const onUpdate = async (limit: WipLimit, patch: Partial<WipLimit>) => {
    setSaving(`${limit.project_key ?? "tenant"}:${limit.status}`);
    setError(null);
    try {
      await api.putWipLimit({ ...limit, ...patch });
      refetch();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(null);
    }
  };

  const onDelete = async (limit: WipLimit) => {
    setSaving(`${limit.project_key ?? "tenant"}:${limit.status}`);
    setError(null);
    try {
      await api.deleteWipLimit(limit.status, limit.project_key);
      refetch();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(null);
    }
  };

  // UI gate: DemoSeedPanel renders ONLY on Forge development environment.
  // Customers on Marketplace production-env installs never see the button
  // — defense in depth alongside the backend's ALLOW_DEMO_SEED gate. Even
  // if a future deploy accidentally enables the backend endpoint on prod,
  // the panel still doesn't render for production installs, so customers
  // can't click it.
  const showDemoSeed = environmentType === "development";

  return (
    <div className="space-y-6">
      {showDemoSeed && <DemoSeedPanel projectKey={projectKey} />}
      <BackfillPanel />
      <StatusIdBackfillPanel />
    <section className="rounded-2xl border border-ink-200 bg-white p-6 shadow-sm dark:border-ink-800 dark:bg-ink-900">
      <header className="mb-4">
        <p className="text-sm uppercase tracking-wide text-ink-400">Settings</p>
        <h3 className="mt-1 text-xl font-semibold">WIP limits</h3>
        <p className="mt-1 text-xs text-ink-500 dark:text-ink-400">
          Per-status caps that turn raw WIP averages into actionable signals.{" "}
          {projectKey ? (
            <>
              These limits are scoped to project <code className="font-mono">{projectKey}</code>.
              Project rows override any tenant-wide defaults.
            </>
          ) : (
            <>These limits apply tenant-wide.</>
          )}{" "}
          See the user manual's WIP limits page for guidance on choosing values.
        </p>
      </header>

      {error && (
        <div className="mb-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-900 dark:border-red-900/50 dark:bg-red-900/20 dark:text-red-100">
          {error}
        </div>
      )}

      <table className="w-full text-sm">
        <thead className="text-left text-xs uppercase tracking-wide text-ink-400">
          <tr>
            <th className="py-2">Status</th>
            <th>Scope</th>
            <th>Max in progress</th>
            <th>Breach minutes</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {limits === null && (
            <tr>
              <td colSpan={5} className="py-4 text-ink-500">
                Loading…
              </td>
            </tr>
          )}
          {limits?.length === 0 && (
            <tr>
              <td colSpan={5} className="py-4 text-ink-500">
                No WIP limits configured. Add one below.
              </td>
            </tr>
          )}
          {limits?.map((L) => (
            <tr
              key={`${L.project_key ?? "tenant"}:${L.status}`}
              className="border-t border-ink-100 dark:border-ink-800"
            >
              <td className="py-2 font-medium">{L.status}</td>
              <td className="text-xs text-ink-500">
                {L.project_key ? `project ${L.project_key}` : "tenant-wide"}
              </td>
              <td>
                <input
                  type="number"
                  min={0}
                  defaultValue={L.max_in_progress}
                  onBlur={(e) => {
                    const v = Number.parseInt(e.target.value, 10);
                    if (!Number.isNaN(v) && v !== L.max_in_progress) {
                      onUpdate(L, { max_in_progress: v });
                    }
                  }}
                  className="w-20 rounded border border-ink-200 bg-white px-2 py-1 text-sm dark:border-ink-700 dark:bg-ink-900"
                />
              </td>
              <td>
                <input
                  type="number"
                  min={0}
                  defaultValue={L.breach_minutes}
                  onBlur={(e) => {
                    const v = Number.parseInt(e.target.value, 10);
                    if (!Number.isNaN(v) && v !== L.breach_minutes) {
                      onUpdate(L, { breach_minutes: v });
                    }
                  }}
                  className="w-20 rounded border border-ink-200 bg-white px-2 py-1 text-sm dark:border-ink-700 dark:bg-ink-900"
                />
              </td>
              <td>
                <button
                  type="button"
                  onClick={() => onDelete(L)}
                  disabled={saving === `${L.project_key ?? "tenant"}:${L.status}`}
                  className="text-xs text-red-600 hover:underline disabled:opacity-50 dark:text-red-400"
                >
                  Remove
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="mt-6">
        <h4 className="text-sm font-semibold">Add a limit</h4>
        <div className="mt-2 flex flex-wrap items-end gap-3">
          <label className="flex flex-col text-xs">
            <span className="text-ink-500 dark:text-ink-400">Status</span>
            <input
              list="known-statuses"
              value={draft.status}
              onChange={(e) => setDraft({ ...draft, status: e.target.value })}
              placeholder="Code Review"
              className="mt-1 rounded border border-ink-200 bg-white px-2 py-1 text-sm dark:border-ink-700 dark:bg-ink-900"
            />
            <datalist id="known-statuses">
              {knownStatuses.map((s) => (
                <option key={s} value={s} />
              ))}
            </datalist>
          </label>
          <label className="flex flex-col text-xs">
            <span className="text-ink-500 dark:text-ink-400">Max in progress</span>
            <input
              type="number"
              min={0}
              value={draft.max_in_progress}
              onChange={(e) => setDraft({ ...draft, max_in_progress: e.target.value })}
              className="mt-1 w-20 rounded border border-ink-200 bg-white px-2 py-1 text-sm dark:border-ink-700 dark:bg-ink-900"
            />
          </label>
          <label className="flex flex-col text-xs">
            <span className="text-ink-500 dark:text-ink-400">Breach minutes</span>
            <input
              type="number"
              min={0}
              value={draft.breach_minutes}
              onChange={(e) => setDraft({ ...draft, breach_minutes: e.target.value })}
              className="mt-1 w-20 rounded border border-ink-200 bg-white px-2 py-1 text-sm dark:border-ink-700 dark:bg-ink-900"
            />
          </label>
          <button
            type="button"
            onClick={onAdd}
            disabled={!draft.status.trim() || saving !== null}
            className="rounded-md bg-ink-900 px-3 py-1.5 text-xs font-medium text-white shadow-sm hover:bg-ink-700 disabled:opacity-50 dark:bg-white dark:text-ink-900 dark:hover:bg-ink-200"
          >
            {saving === draft.status ? "Saving…" : "Add limit"}
          </button>
        </div>
        <p className="mt-2 text-xs text-ink-500 dark:text-ink-400">
          <strong>Breach minutes</strong>: how long WIP must stay over the limit before the
          alerts pipeline fires a <code className="font-mono">wip_breach</code> alert. Set to{" "}
          <code className="font-mono">0</code> to disable the alert and only show breach
          indicators visually.
        </p>
      </div>
    </section>
      <AlertDestinationsPanel />
      <AlertRulesPanel knownStatuses={knownStatuses} />
      <WorkSchedulePanel />
      <TenantSettingsPanel knownStatuses={knownStatuses} />
    </div>
  );
}

// ----- Backfill panel (ADR-0033) -------------------------------------------
//
// Polling-driven, NOT a JS loop. The Forge consumer runs the actual backfill
// in the background (per ADR-0033's locked outcome #2 — no tab residency).
// This panel just polls /api/forge/sync/state to render whatever state the
// backend currently has. Customer can close the tab, walk away, come back
// hours later; the polling restarts from the current backend state.
//
// The "Start" button invokes the Forge `startBackfill` resolver, which
// (a) flips backend status to running and (b) pushes a task onto the
// in-Forge event queue for the consumer to pick up. Idempotent re-clicks
// during a run are no-ops on the backend.
//
// 50,000-issue cap reached state is rendered distinct from a normal
// completion (per customer-copy-adr-0033.md §6) — surfaces the support@
// mailto for cap-extension requests.

const BACKFILL_CAP = 50_000;

function BackfillPanel() {
  const [syncState, setSyncState] = useState<SyncState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  const refresh = useCallback(() => {
    api.getSyncState().then(
      (s) => setSyncState(s),
      (e) => setError((e as Error).message),
    );
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // ADR-0033 polling: 5s while running, 30s while pending, stop while
  // terminal (completed / failed / null). Cleanup on status change so
  // we don't leak intervals across state transitions.
  const status = syncState?.backfill.status ?? null;
  useEffect(() => {
    if (status === "running") {
      const id = window.setInterval(refresh, 5_000);
      return () => window.clearInterval(id);
    }
    if (status === "pending") {
      const id = window.setInterval(refresh, 30_000);
      return () => window.clearInterval(id);
    }
    return undefined;
  }, [status, refresh]);

  const start = async () => {
    if (starting) return;
    setStarting(true);
    setError(null);
    try {
      // Forge resolver call (NOT a backend HTTP request) — the resolver
      // is what can push onto the in-Forge event queue. See
      // forge-prod/src/resolvers/dashboard.ts → startBackfill.
      await invoke("startBackfill");
      // Refresh immediately so the user sees status flip to pending /
      // running. Polling takes over from here.
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setStarting(false);
    }
  };

  const backfill = syncState?.backfill;
  const running = status === "running";
  const pending = status === "pending";
  const completed = status === "completed";
  const failed = status === "failed";
  const capReached = completed && (backfill?.processedIssues ?? 0) >= BACKFILL_CAP;

  return (
    <section className="rounded-2xl border border-ink-200 bg-white p-6 shadow-sm dark:border-ink-800 dark:bg-ink-900">
      <header className="mb-4">
        <p className="text-sm uppercase tracking-wide text-ink-400">Settings</p>
        <h3 className="mt-1 text-xl font-semibold">Historical backfill</h3>
        <p className="mt-1 text-xs text-ink-500 dark:text-ink-400">
          Pulls every Jira issue your install can read into Jira Flow Intelligence, with no
          time floor — fills the dashboard with full history. New installs run this
          automatically in the background; you don't need to keep this tab open.
          We'll email you when it finishes.
        </p>
      </header>

      {error && (
        <div className="mb-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-900 dark:border-red-900/50 dark:bg-red-900/20 dark:text-red-100">
          {error}
        </div>
      )}

      <AdminEmailRow
        currentEmail={syncState?.adminContactEmail ?? null}
        onChange={refresh}
        onError={setError}
      />

      {(pending || running) && (
        <div className="mb-4 rounded-md border border-ink-200 bg-ink-50 p-3 text-sm dark:border-ink-700 dark:bg-ink-900/40">
          <p className="font-medium">
            {pending ? "Backfill queued" : "Backfill in progress"}
          </p>
          <p className="mt-1 text-xs text-ink-500 dark:text-ink-400">
            {backfill?.processedIssues != null
              ? `${backfill.processedIssues.toLocaleString()} issues processed`
              : "Starting…"}
            {backfill?.startedAt && (
              <>
                {" · started "}
                {new Date(backfill.startedAt).toLocaleString()}
              </>
            )}
            {running && " · refreshes every 5s"}
          </p>
          <p className="mt-2 text-xs text-ink-500 dark:text-ink-400">
            Running in the background. You can close this tab — we'll email{" "}
            {syncState?.adminContactEmail ?? "you (once you add an email above)"} when
            it finishes.
          </p>
        </div>
      )}

      {/* Per customer-copy-adr-0033.md §6 — cap-reached has its own state. */}
      {capReached && backfill?.completedAt && (
        <div className="mb-4 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900/50 dark:bg-amber-900/20 dark:text-amber-100">
          <p className="font-medium">
            Backfill complete at the 50,000-issue cap.
          </p>
          <p className="mt-1 text-xs">
            Your site has more historical issues than this initial run indexed.{" "}
            <a
              className="underline"
              href="mailto:support@example.com?subject=Backfill%20cap%20extension%20request"
            >
              Email support
            </a>{" "}
            if you want the cap extended — we do it for free.
          </p>
        </div>
      )}

      {/* Per customer-copy-adr-0033.md §5 — informational, non-cap completion. */}
      {completed && !capReached && backfill?.completedAt && (
        <div className="mb-4 rounded-md border border-emerald-200 bg-emerald-50/50 p-3 text-sm text-emerald-900 dark:border-emerald-900/50 dark:bg-emerald-900/20 dark:text-emerald-100">
          <p className="font-medium">Backfill complete.</p>
          <p className="mt-1 text-xs">
            {(backfill.processedIssues ?? 0).toLocaleString()} issues indexed on{" "}
            {new Date(backfill.completedAt).toLocaleString()}.
            <br />
            The dashboard updates automatically as you change tickets in Jira from
            here. No further action needed.
          </p>
        </div>
      )}

      {failed && (
        <div className="mb-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-900 dark:border-red-900/50 dark:bg-red-900/20 dark:text-red-100">
          <p className="font-medium">Last backfill failed.</p>
          <p className="mt-1 text-xs">{backfill?.error ?? "unknown error"}</p>
          <p className="mt-1 text-xs">Click Retry below. Most failures resolve on the second attempt.</p>
        </div>
      )}

      <button
        type="button"
        onClick={start}
        disabled={starting || pending || running}
        className="rounded-md bg-ink-900 px-3 py-1.5 text-xs font-medium text-white shadow-sm hover:bg-ink-700 disabled:opacity-50 dark:bg-white dark:text-ink-900 dark:hover:bg-ink-200"
      >
        {starting
          ? "Starting…"
          : pending || running
            ? "Running in background…"
            : completed
              ? "Run another backfill"
              : failed
                ? "Retry backfill"
                : "Start historical backfill"}
      </button>
      <p className="mt-2 text-xs text-ink-500 dark:text-ink-400">
        Cap: 50,000 issues per run. Pulls in ~100-issue batches. ~3 minutes
        wall-clock for 5,000 issues; longer for big sites — but you don't need to
        wait. Closing the tab doesn't pause anything; the background queue runs
        regardless.
      </p>
    </section>
  );
}

// ----- Admin contact email row (ADR-0033) ----------------------------------
//
// Renders the current admin_contact_email value (the destination for SES
// completion / failure / cap-reached emails) with inline edit + clear.
// Customer can clear the email to opt out of notifications (per the
// customer-copy doc's unsubscribe path).

function AdminEmailRow(props: {
  currentEmail: string | null;
  onChange: () => void;
  onError: (msg: string | null) => void;
}) {
  const { currentEmail, onChange, onError } = props;
  const [draft, setDraft] = useState(currentEmail ?? "");
  const [saving, setSaving] = useState(false);

  // Keep the input in sync when the server-side value changes (e.g.,
  // a different admin set it from another tab). Don't clobber user edits
  // in flight — only update when the saved value actually differs from
  // the last draft seed.
  useEffect(() => {
    setDraft(currentEmail ?? "");
  }, [currentEmail]);

  const save = async () => {
    if (saving) return;
    setSaving(true);
    onError(null);
    try {
      const next = draft.trim();
      await api.setAdminEmail(next ? next : null);
      onChange();
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const isDirty = draft.trim() !== (currentEmail ?? "");

  return (
    <div className="mb-4 rounded-md border border-ink-200 bg-ink-50/50 p-3 dark:border-ink-800 dark:bg-ink-900/30">
      <label className="block text-xs font-medium text-ink-700 dark:text-ink-200">
        Notification email
      </label>
      <p className="mt-1 text-xs text-ink-500 dark:text-ink-400">
        Where Jira Flow Intelligence sends backfill completion / failure / cap-reached
        notifications. Clear the field to stop receiving these emails.
      </p>
      <div className="mt-2 flex items-center gap-2">
        <input
          type="email"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="you@example.com"
          className="flex-1 rounded-md border border-ink-200 bg-white px-2 py-1 text-xs text-ink-900 dark:border-ink-700 dark:bg-ink-900 dark:text-ink-100"
        />
        <button
          type="button"
          onClick={save}
          disabled={saving || !isDirty}
          className="rounded-md bg-ink-900 px-3 py-1 text-xs font-medium text-white shadow-sm hover:bg-ink-700 disabled:opacity-50 dark:bg-white dark:text-ink-900 dark:hover:bg-ink-200"
        >
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}

// ----- Demo seed panel (testing tooling) -----------------------------------
//
// Restored while running the heavy-testing period per maintainer
// direction. Calls /api/dev/seed-demo on the backend,
// which is gated by ALLOW_DEMO_SEED (per-env config; True for prod
// during testing per EnvConfig.allow_demo_seed). Wipes the calling
// tenant's data and writes 250 backdated issues + 5 sprints + a
// designed Review-stage bottleneck — the same fixture the marketplace
// listing screenshots were captured against.
//
// projectKey scopes the seeded issues — the dashboard's chart queries
// all filter by project_key, so a mismatch produces a successful seed
// but an empty dashboard. The parent SettingsTab passes the active
// project from the Forge context.
//
// When the testing period ends, flip EnvConfig.allow_demo_seed back to
// False on prod (the backend's /api/dev/seed-demo will then 404 and
// this panel's button calls will fail cleanly). The panel itself can
// be left in place — server-side gating is the load-bearing protection.

// Two fixtures are exposed: the original Marketplace-shape dataset (250
// issues + designed Review-stage bottleneck — what the listing screenshots
// use), and the content-screenshots fixture (5 scripted DEMO
// tickets + 80–120 background tickets + work schedule + external-blocking
// statuses configured — purpose-built for content-cycle screenshots).
// Two buttons, not a dropdown — discoverable, no hidden state, matches the
// maintainer direction.
interface SeedResult {
  issues: number;
  transitions: number;
  slices: number;
}

function DemoSeedPanel({ projectKey }: { projectKey: string | undefined }) {
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<SeedResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const onSeed = async () => {
    if (!projectKey) {
      setError("Project key not yet known — wait for the dashboard to finish loading.");
      return;
    }
    setRunning(true);
    setError(null);
    try {
      const r = (await api.seedDemo(projectKey)) as SeedResult;
      setResult(r);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  };

  return (
    <section className="rounded-2xl border border-amber-300 bg-amber-50 p-6 shadow-sm dark:border-amber-900/50 dark:bg-amber-900/20">
      <header className="mb-3">
        <p className="text-xs font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-300">
          Demo seed (testing)
        </p>
        <h3 className="mt-1 text-lg font-semibold">Populate synthetic data</h3>
        <p className="mt-1 text-xs text-amber-900/80 dark:text-amber-100/80">
          Wipes tenant data and writes synthetic issues under the current
          project ({projectKey ?? "loading…"}). Idempotent — clicking multiple
          times produces the same dataset. Gated server-side by
          ALLOW_DEMO_SEED; flip to False on prod before public-customer launch.
        </p>
      </header>
      <div>
        <button
          type="button"
          onClick={onSeed}
          disabled={running || !projectKey}
          className="rounded-md bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-700 disabled:opacity-50"
        >
          {running ? "Seeding…" : "Load demo data"}
        </button>
        <p className="mt-1 text-xs text-amber-900/70 dark:text-amber-100/70">
          250 issues + Review-stage bottleneck. Marketplace listing screenshots.
        </p>
      </div>
      {result && (
        <p className="mt-3 text-sm text-amber-900 dark:text-amber-100">
          ✓ Seeded {result.issues} issues, {result.transitions} transitions,
          {" "}
          {result.slices} time slices. Refresh the page to see the dashboard
          populate.
        </p>
      )}
      {error && (
        <p className="mt-3 text-sm text-red-700 dark:text-red-300">
          Failed: {error}
        </p>
      )}
    </section>
  );
}

// ADR-0046 — Path B retroactive backfill of legacy NULL `status_id` rows.
// Companion to ADR-0045 (status-ID-aware aggregation across renames):
// Path A makes the property true going forward; Path B (this panel)
// closes the historical half by fetching the current Jira status list,
// matching by name, and populating status_id on legacy NULL rows.
//
// Idempotent — re-clicking after a successful run is a no-op. The
// resolver does the Jira /rest/api/3/status fetch with api.asApp() (no
// user session required) and forwards the result to the backend.
function StatusIdBackfillPanel() {
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<{
    updated_transitions: number;
    updated_slices: number;
    unresolved_names: string[];
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const onRun = async () => {
    setRunning(true);
    setError(null);
    try {
      const r = (await invoke("backfillStatusIds")) as {
        updated_transitions: number;
        updated_slices: number;
        unresolved_names: string[];
      };
      setResult(r);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  };

  return (
    <section className="rounded-2xl border border-ink-200 bg-white p-6 shadow-sm dark:border-ink-800 dark:bg-ink-900">
      <header className="mb-3">
        <p className="text-sm uppercase tracking-wide text-ink-400">Maintenance</p>
        <h3 className="mt-1 text-xl font-semibold">Backfill historical status IDs</h3>
        <p className="mt-1 text-sm text-ink-500 dark:text-ink-400">
          One-time pass that fetches your project's current status list from
          Jira and populates stable status IDs on legacy rows that predate
          ADR-0045 (June 2026). Renamed statuses then merge correctly across
          the rename in every chart. Idempotent — safe to click again if a
          previous run was interrupted.
        </p>
      </header>
      <button
        type="button"
        onClick={onRun}
        disabled={running}
        className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-dark disabled:opacity-50"
      >
        {running ? "Backfilling…" : "Run backfill"}
      </button>
      {result && (
        <div className="mt-3 text-sm text-ink-700 dark:text-ink-200">
          <p>
            ✓ Updated {result.updated_transitions} transition columns and
            {" "}
            {result.updated_slices} time slices.
          </p>
          {result.unresolved_names.length > 0 && (
            <p className="mt-2 text-amber-700 dark:text-amber-300">
              ⚠ {result.unresolved_names.length} status name(s) didn't match
              the current Jira workflow (likely renamed-then-deleted):
              {" "}
              <span className="font-mono">{result.unresolved_names.join(", ")}</span>.
              Restore the status in Jira and re-run, or accept the orphan.
            </p>
          )}
        </div>
      )}
      {error && (
        <p className="mt-3 text-sm text-red-700 dark:text-red-300">
          Failed: {error}
        </p>
      )}
    </section>
  );
}
