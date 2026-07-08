// Alert delivery destinations (ADR-0037 phase 4).
//
// Manage where fired alerts are pushed: email, Slack, Microsoft Teams (incoming
// webhook URL paste). Tenant-default destinations apply to any rule without an
// explicit per-rule binding (the per-rule override lives in AlertRulesPanel).
// Surfaces delivery failures + the auto-pause state so the customer always has
// an answer to "did the alert fire?" (CLAUDE.md rule #9).

import { useEffect, useState } from "react";

import { api } from "../lib/requestRemote";
import type { AlertDestination, AlertDestinationType } from "../lib/types";

const SETUP_DOCS: Partial<Record<AlertDestinationType, string>> = {
  slack: "https://example.com/docs/slack-setup",
  teams: "https://example.com/docs/teams-setup",
};

interface Draft {
  type: AlertDestinationType;
  name: string;
  value: string; // email address or webhook URL
  isDefault: boolean;
}

const EMPTY_DRAFT: Draft = { type: "email", name: "", value: "", isDefault: true };

export function AlertDestinationsPanel() {
  const [destinations, setDestinations] = useState<AlertDestination[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);

  const refresh = () => {
    api.getAlertDestinations().then(
      (r) => setDestinations(r.destinations),
      (e) => setError((e as Error).message),
    );
  };
  useEffect(refresh, []);

  const add = async () => {
    if (!draft.name.trim() || !draft.value.trim()) return;
    setBusy("add");
    setError(null);
    const config =
      draft.type === "email" ? { address: draft.value.trim() } : { webhook_url: draft.value.trim() };
    try {
      await api.putAlertDestination({
        id: `${draft.type}-${Date.now().toString(36)}`,
        type: draft.type,
        name: draft.name.trim(),
        config,
        is_tenant_default: draft.isDefault,
        status: "active",
      });
      setDraft(EMPTY_DRAFT);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const sendTest = async (d: AlertDestination) => {
    setBusy(`test-${d.id}`);
    setError(null);
    try {
      await api.testAlertDestination(d.id);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const remove = async (d: AlertDestination) => {
    setBusy(`del-${d.id}`);
    setError(null);
    try {
      await api.deleteAlertDestination(d.id);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const reEnable = async (d: AlertDestination) => {
    setBusy(`en-${d.id}`);
    setError(null);
    try {
      // config omitted → backend preserves the stored webhook/address.
      await api.putAlertDestination({
        id: d.id,
        type: d.type,
        name: d.name,
        config: {},
        is_tenant_default: d.is_tenant_default,
        status: "active",
      });
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="rounded-2xl border border-ink-200 bg-white p-6 shadow-sm dark:border-ink-800 dark:bg-ink-900">
      <header className="mb-4">
        <p className="text-sm uppercase tracking-wide text-ink-400">Settings</p>
        <h3 className="mt-1 text-xl font-semibold">Alert delivery destinations</h3>
        <p className="mt-1 text-xs text-ink-500 dark:text-ink-400">
          Push fired alerts to email, Slack, or Microsoft Teams. Tenant-default destinations
          receive every rule's alerts unless a rule sets its own destinations below.
        </p>
      </header>

      {error && (
        <div className="mb-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-900 dark:border-red-900/50 dark:bg-red-900/20 dark:text-red-100">
          {error}
        </div>
      )}

      {destinations === null && <p className="text-sm text-ink-500">Loading…</p>}
      {destinations?.length === 0 && (
        <p className="text-sm text-ink-500">
          No destinations yet. Add one below — until then, alerts show in-product only.
        </p>
      )}

      {destinations && destinations.length > 0 && (
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase tracking-wide text-ink-400">
            <tr>
              <th className="py-2">Name</th>
              <th>Type</th>
              <th>Status</th>
              <th>Last test</th>
              <th>Failures (24h)</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {destinations.map((d) => {
              const paused = d.status === "disabled";
              return (
                <tr key={d.id} className="border-t border-ink-100 dark:border-ink-800">
                  <td className="py-2 font-medium">
                    {d.name}
                    {d.is_tenant_default && (
                      <span className="ml-2 rounded bg-ink-100 px-1.5 py-0.5 text-[10px] uppercase text-ink-500 dark:bg-ink-800 dark:text-ink-400">
                        default
                      </span>
                    )}
                  </td>
                  <td className="text-xs">{d.type}</td>
                  <td className="text-xs">
                    {paused ? (
                      <span className="font-semibold text-red-600 dark:text-red-400">paused</span>
                    ) : (
                      "active"
                    )}
                  </td>
                  <td className="text-xs text-ink-500">{d.last_test_status ?? "—"}</td>
                  <td className="text-xs">
                    {d.recent_failure_count > 0 ? (
                      <span className="font-semibold text-red-600 dark:text-red-400">
                        {d.recent_failure_count}
                      </span>
                    ) : (
                      "0"
                    )}
                  </td>
                  <td className="text-right">
                    {paused && (
                      <button
                        type="button"
                        onClick={() => reEnable(d)}
                        disabled={busy !== null}
                        className="text-xs text-emerald-700 hover:underline disabled:opacity-50 dark:text-emerald-400"
                      >
                        Re-enable
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => sendTest(d)}
                      disabled={busy !== null}
                      className="ml-3 text-xs text-ink-600 hover:underline disabled:opacity-50 dark:text-ink-300"
                    >
                      {busy === `test-${d.id}` ? "Sending…" : "Send test"}
                    </button>
                    <button
                      type="button"
                      onClick={() => remove(d)}
                      disabled={busy !== null}
                      className="ml-3 text-xs text-red-600 hover:underline disabled:opacity-50 dark:text-red-400"
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      <div className="mt-6">
        <h4 className="text-sm font-semibold">Add a destination</h4>
        <div className="mt-2 flex flex-wrap items-end gap-3">
          <label className="flex flex-col text-xs">
            <span className="text-ink-500 dark:text-ink-400">Type</span>
            <select
              value={draft.type}
              onChange={(e) =>
                setDraft({ ...draft, type: e.target.value as AlertDestinationType })
              }
              className="mt-1 rounded border border-ink-200 bg-white px-2 py-1 text-sm dark:border-ink-700 dark:bg-ink-900"
            >
              <option value="email">email</option>
              <option value="slack">slack</option>
              <option value="teams">teams</option>
            </select>
            {SETUP_DOCS[draft.type] && (
              <a
                href={SETUP_DOCS[draft.type]}
                target="_blank"
                rel="noreferrer"
                className="mt-1 text-ink-400 underline hover:text-ink-600 dark:hover:text-ink-200"
              >
                How to set up {draft.type === "slack" ? "Slack" : "Teams"} delivery →
              </a>
            )}
          </label>
          <label className="flex flex-col text-xs">
            <span className="text-ink-500 dark:text-ink-400">Name</span>
            <input
              value={draft.name}
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              placeholder={draft.type === "email" ? "Ops inbox" : "#eng-alerts"}
              className="mt-1 rounded border border-ink-200 bg-white px-2 py-1 text-sm dark:border-ink-700 dark:bg-ink-900"
            />
          </label>
          <label className="flex flex-col text-xs">
            <span className="text-ink-500 dark:text-ink-400">
              {draft.type === "email" ? "Email address" : "Webhook URL"}
            </span>
            <input
              value={draft.value}
              onChange={(e) => setDraft({ ...draft, value: e.target.value })}
              placeholder={
                draft.type === "email" ? "alerts@team.com" : "https://hooks.slack.com/services/…"
              }
              className="mt-1 w-72 rounded border border-ink-200 bg-white px-2 py-1 text-sm dark:border-ink-700 dark:bg-ink-900"
            />
          </label>
          <label className="flex items-center gap-1.5 text-xs">
            <input
              type="checkbox"
              checked={draft.isDefault}
              onChange={(e) => setDraft({ ...draft, isDefault: e.target.checked })}
            />
            <span className="text-ink-600 dark:text-ink-300">Tenant default</span>
          </label>
          <button
            type="button"
            onClick={add}
            disabled={busy !== null || !draft.name.trim() || !draft.value.trim()}
            className="rounded-md bg-ink-900 px-3 py-1.5 text-xs font-medium text-white shadow-sm hover:bg-ink-700 disabled:opacity-50 dark:bg-white dark:text-ink-900 dark:hover:bg-ink-200"
          >
            {busy === "add" ? "Adding…" : "Add destination"}
          </button>
        </div>
      </div>
    </section>
  );
}
