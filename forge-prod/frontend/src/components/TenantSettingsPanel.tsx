// Tenant-wide configuration.
//
// Three sections:
//
// 1. Workflow status sets — active / done / terminal. Each is a chip
//    list editor with a typeahead from `knownStatuses`. NULL override
//    means "use the bundled default" (e.g., done = ["Done","Closed",
//    "Resolved"]); admin can replace with their own list.
//
// 2. Bottleneck thresholds — three numeric sliders that gate the
//    scoring engine. Sane bounds enforced server-side; UI shows the
//    current effective value and whether it's a default or override.
//
// 3. Custom field IDs — Story Points and Sprint. The heuristic
//    Sprint detection still runs as a fallback, but admins on sites
//    with quirky workflows can pin the exact field here.
//
// Save semantics: every field is a separate input. Save button POSTs
// the whole object, replacing all overrides. "Reset to defaults" sends
// every field as null in one call.

import { useEffect, useState } from "react";

import { api } from "../lib/requestRemote";
import type { TenantSettings, TenantSettingsIn } from "../lib/types";

interface Props {
  knownStatuses: string[];
}

interface Draft {
  active_statuses: string[] | null;
  done_statuses: string[] | null;
  terminal_statuses: string[] | null;
  // ADR-0042: external-blocking statuses are excluded from bottleneck
  // attribution but still recorded in slice data + charts. `null` =
  // inherit Settings default (currently []); `[]` = explicit "no
  // external-blocking statuses for this tenant".
  external_blocking_statuses: string[] | null;
  independent_done_terminal_lists: boolean;
  bottleneck_time_ratio_threshold: number | null;
  bottleneck_wip_ratio_threshold: number | null;
  bottleneck_throughput_delta_threshold: number | null;
  story_points_field_id: string;
  sprint_field_id: string;
}

function draftFrom(s: TenantSettings): Draft {
  return {
    active_statuses: s.active_statuses_override,
    done_statuses: s.done_statuses_override,
    terminal_statuses: s.terminal_statuses_override,
    external_blocking_statuses: s.external_blocking_statuses_override,
    independent_done_terminal_lists: s.independent_done_terminal_lists,
    bottleneck_time_ratio_threshold: s.bottleneck_time_ratio_threshold_override,
    bottleneck_wip_ratio_threshold: s.bottleneck_wip_ratio_threshold_override,
    bottleneck_throughput_delta_threshold:
      s.bottleneck_throughput_delta_threshold_override,
    story_points_field_id: s.story_points_field_id ?? "",
    sprint_field_id: s.sprint_field_id ?? "",
  };
}

function payloadFrom(d: Draft): TenantSettingsIn {
  return {
    active_statuses: d.active_statuses,
    done_statuses: d.done_statuses,
    terminal_statuses: d.terminal_statuses,
    external_blocking_statuses: d.external_blocking_statuses,
    independent_done_terminal_lists: d.independent_done_terminal_lists,
    bottleneck_time_ratio_threshold: d.bottleneck_time_ratio_threshold,
    bottleneck_wip_ratio_threshold: d.bottleneck_wip_ratio_threshold,
    bottleneck_throughput_delta_threshold: d.bottleneck_throughput_delta_threshold,
    story_points_field_id: d.story_points_field_id.trim() || null,
    sprint_field_id: d.sprint_field_id.trim() || null,
  };
}

export function TenantSettingsPanel({ knownStatuses }: Props) {
  const [settings, setSettings] = useState<TenantSettings | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [savedNote, setSavedNote] = useState<string | null>(null);

  const refresh = () => {
    api.getTenantSettings().then(
      (s) => {
        setSettings(s);
        setDraft(draftFrom(s));
      },
      (e) => setError((e as Error).message),
    );
  };

  useEffect(refresh, []);

  const save = async () => {
    if (!draft) return;
    setBusy(true);
    setError(null);
    setSavedNote(null);
    try {
      const updated = await api.putTenantSettings(payloadFrom(draft));
      setSettings(updated);
      setDraft(draftFrom(updated));
      setSavedNote("Saved.");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const reset = async () => {
    setBusy(true);
    setError(null);
    setSavedNote(null);
    try {
      const updated = await api.putTenantSettings({});
      setSettings(updated);
      setDraft(draftFrom(updated));
      setSavedNote("Reset to defaults.");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (settings === null || draft === null) {
    return (
      <section className="rounded-2xl border border-ink-200 bg-white p-6 shadow-sm dark:border-ink-800 dark:bg-ink-900">
        <p className="text-sm text-ink-500">Loading tenant settings…</p>
      </section>
    );
  }

  return (
    <section className="rounded-2xl border border-ink-200 bg-white p-6 shadow-sm dark:border-ink-800 dark:bg-ink-900">
      <header className="mb-4">
        <p className="text-sm uppercase tracking-wide text-ink-400">Settings</p>
        <h3 className="mt-1 text-xl font-semibold">Tenant configuration</h3>
        <p className="mt-1 text-xs text-ink-500 dark:text-ink-400">
          Tenant-wide overrides for the bundled defaults. Each field can be left as
          default (shows the inherited value) or replaced with a custom value. Reset
          to defaults removes every override at once.
        </p>
      </header>

      {error && (
        <div className="mb-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-900 dark:border-red-900/50 dark:bg-red-900/20 dark:text-red-100">
          {error}
        </div>
      )}
      {savedNote && (
        <div className="mb-4 rounded-md border border-emerald-300 bg-emerald-50 p-3 text-sm text-emerald-900 dark:border-emerald-900/50 dark:bg-emerald-900/20 dark:text-emerald-100">
          {savedNote}
        </div>
      )}

      <div className="space-y-6">
        <StatusListRow
          label="Active statuses"
          help="Stages where work is being actively done. Drives the WIP and active-time signals."
          override={draft.active_statuses}
          effective={settings.effective_active_statuses}
          knownStatuses={knownStatuses}
          onChange={(v) => setDraft({ ...draft, active_statuses: v })}
        />
        <StatusListRow
          label="Done statuses"
          help="Stages that count as 'shipped'. Drives cycle-time and throughput metrics."
          override={draft.done_statuses}
          effective={settings.effective_done_statuses}
          knownStatuses={knownStatuses}
          onChange={(v) => setDraft({ ...draft, done_statuses: v })}
        />
        <StatusListRow
          label="Terminal statuses"
          help="Workflow endpoints (done + cancelled/won't-do/etc.). Excluded from the CFD chart."
          override={draft.terminal_statuses}
          effective={settings.effective_terminal_statuses}
          knownStatuses={knownStatuses}
          onChange={(v) => setDraft({ ...draft, terminal_statuses: v })}
        />
        <StatusListRow
          label="External-blocking statuses"
          help="Statuses where work is paused waiting on a third party (customer, vendor, external review). Time spent in these statuses is still tracked everywhere, but excluded from 'where is the team stuck?' attribution. Leave blank if every status in your workflow is team-controllable."
          override={draft.external_blocking_statuses}
          effective={settings.effective_external_blocking_statuses}
          knownStatuses={knownStatuses}
          allowEmpty
          onChange={(v) => setDraft({ ...draft, external_blocking_statuses: v })}
        />

        <ThresholdRow
          label="Time-ratio threshold"
          help="A status's avg-time must be at least this many times its prior-window avg to count toward the bottleneck score."
          override={draft.bottleneck_time_ratio_threshold}
          effective={settings.effective_bottleneck_time_ratio_threshold}
          min={1.0}
          max={10.0}
          step={0.1}
          onChange={(v) =>
            setDraft({ ...draft, bottleneck_time_ratio_threshold: v })
          }
        />
        <ThresholdRow
          label="WIP-ratio threshold"
          help="Same idea, for WIP avg vs prior. Default 1.2."
          override={draft.bottleneck_wip_ratio_threshold}
          effective={settings.effective_bottleneck_wip_ratio_threshold}
          min={1.0}
          max={10.0}
          step={0.1}
          onChange={(v) =>
            setDraft({ ...draft, bottleneck_wip_ratio_threshold: v })
          }
        />
        <ThresholdRow
          label="Throughput-delta threshold"
          help="Negative numbers — a drop in throughput by this fraction or more contributes to bottleneck score. Default -0.2 (= 20% drop)."
          override={draft.bottleneck_throughput_delta_threshold}
          effective={settings.effective_bottleneck_throughput_delta_threshold}
          min={-1.0}
          max={1.0}
          step={0.05}
          onChange={(v) =>
            setDraft({ ...draft, bottleneck_throughput_delta_threshold: v })
          }
        />

        <FieldIdRow
          label="Story points custom field"
          help="Default: tries customfield_10016 / 10026 / 10002 / 10004 in order. Override here to pin one."
          value={draft.story_points_field_id}
          placeholder="customfield_10016"
          onChange={(v) => setDraft({ ...draft, story_points_field_id: v })}
        />
        <FieldIdRow
          label="Sprint custom field"
          help="Default: tries customfield_10020 / 10010 / 10000 / 10001, then heuristic shape probe (Sprint-shaped arrays). Override here to skip the probe."
          value={draft.sprint_field_id}
          placeholder="customfield_10020"
          onChange={(v) => setDraft({ ...draft, sprint_field_id: v })}
        />
      </div>

      {/* ADR-0038 / CLAUDE.md rule #10 — escape hatches for the rare advanced
          workflow live behind an Advanced expander, collapsed by default so
          the 95% who don't need them never see the cognitive load. */}
      <details className="mt-6 rounded-lg border border-ink-200 dark:border-ink-700">
        <summary className="cursor-pointer px-3 py-2 text-sm font-medium text-ink-700 dark:text-ink-200">
          Advanced settings
        </summary>
        <div className="space-y-4 border-t border-ink-200 px-3 py-3 dark:border-ink-700">
          <label className="flex cursor-pointer items-start gap-2">
            <input
              type="checkbox"
              checked={draft.independent_done_terminal_lists}
              onChange={(e) =>
                setDraft({
                  ...draft,
                  independent_done_terminal_lists: e.target.checked,
                })
              }
              className="mt-0.5"
            />
            <div>
              <p className="text-sm font-medium">
                Manage Done and Terminal as fully independent lists
              </p>
              <p className="mt-1 text-xs text-ink-500 dark:text-ink-400">
                By default, Jira Flow Intelligence treats every Done status as
                out-of-flight for bottleneck detection and CFD exclusion.
                Enable this only if you have a workflow where Done is a
                transient state (e.g. Done → Verified → Released) and you
                want bottlenecks within Done flagged. Most teams should
                leave this off.
              </p>
            </div>
          </label>
        </div>
      </details>

      <div className="mt-6 flex items-center gap-3">
        <button
          type="button"
          onClick={save}
          disabled={busy}
          className="rounded-md bg-ink-900 px-3 py-1.5 text-xs font-medium text-white shadow-sm hover:bg-ink-700 disabled:opacity-50 dark:bg-white dark:text-ink-900 dark:hover:bg-ink-200"
        >
          {busy ? "Saving…" : "Save"}
        </button>
        <button
          type="button"
          onClick={reset}
          disabled={busy}
          className="rounded-md border border-ink-200 bg-white px-3 py-1.5 text-xs text-ink-700 hover:bg-ink-50 disabled:opacity-50 dark:border-ink-700 dark:bg-ink-900 dark:text-ink-200 dark:hover:bg-ink-800"
        >
          Reset all to defaults
        </button>
      </div>
    </section>
  );
}

function StatusListRow({
  label,
  help,
  override,
  effective,
  knownStatuses,
  onChange,
  allowEmpty = false,
}: {
  label: string;
  help: string;
  override: string[] | null;
  effective: string[];
  knownStatuses: string[];
  onChange: (v: string[] | null) => void;
  // ADR-0042: when true (external-blocking statuses), an explicit empty
  // list is a valid saved value meaning "no external-blocking statuses
  // for this tenant." When false (the safe default for active/done/
  // terminal), removing the last chip falls back to null (= use default).
  allowEmpty?: boolean;
}) {
  const [chip, setChip] = useState("");
  const list = override ?? effective;
  const isOverride = override !== null;

  const add = () => {
    const v = chip.trim();
    if (!v) return;
    const next = [...list];
    if (!next.includes(v)) next.push(v);
    onChange(next);
    setChip("");
  };
  const remove = (s: string) => {
    const next = list.filter((x) => x !== s);
    if (next.length > 0) {
      onChange(next);
    } else {
      onChange(allowEmpty ? [] : null);
    }
  };

  return (
    <div>
      <div className="flex items-baseline justify-between">
        <p className="text-sm font-medium">{label}</p>
        <span className="text-xs text-ink-500">
          {isOverride ? "override" : "default"}
        </span>
      </div>
      <p className="mt-1 text-xs text-ink-500 dark:text-ink-400">{help}</p>
      <div className="mt-2 flex flex-wrap gap-1">
        {list.map((s) => (
          <span
            key={s}
            className="inline-flex items-center gap-1 rounded-full bg-ink-100 px-2 py-0.5 text-xs dark:bg-ink-800"
          >
            {s}
            <button
              type="button"
              onClick={() => remove(s)}
              className="text-ink-500 hover:text-red-600"
              aria-label={`Remove ${s}`}
            >
              ×
            </button>
          </span>
        ))}
      </div>
      <div className="mt-2 flex items-center gap-2">
        <input
          list={`status-${label.replace(/\s+/g, "-")}`}
          value={chip}
          onChange={(e) => setChip(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }}
          placeholder="Add status…"
          className="rounded border border-ink-200 bg-white px-2 py-1 text-xs dark:border-ink-700 dark:bg-ink-900"
        />
        <datalist id={`status-${label.replace(/\s+/g, "-")}`}>
          {knownStatuses.map((s) => (
            <option key={s} value={s} />
          ))}
        </datalist>
        <button
          type="button"
          onClick={add}
          disabled={!chip.trim()}
          className="text-xs text-ink-600 hover:underline disabled:opacity-50 dark:text-ink-300"
        >
          Add
        </button>
        {isOverride && (
          <button
            type="button"
            onClick={() => onChange(null)}
            className="text-xs text-ink-500 hover:underline dark:text-ink-400"
          >
            Reset to default
          </button>
        )}
      </div>
    </div>
  );
}

function ThresholdRow({
  label,
  help,
  override,
  effective,
  min,
  max,
  step,
  onChange,
}: {
  label: string;
  help: string;
  override: number | null;
  effective: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number | null) => void;
}) {
  const value = override ?? effective;
  const isOverride = override !== null;
  return (
    <div>
      <div className="flex items-baseline justify-between">
        <p className="text-sm font-medium">{label}</p>
        <span className="text-xs text-ink-500">
          {isOverride ? "override" : "default"}
        </span>
      </div>
      <p className="mt-1 text-xs text-ink-500 dark:text-ink-400">{help}</p>
      <div className="mt-2 flex items-center gap-3">
        <input
          type="number"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(e) => {
            const v = Number.parseFloat(e.target.value);
            onChange(Number.isNaN(v) ? null : v);
          }}
          className="w-24 rounded border border-ink-200 bg-white px-2 py-1 text-sm dark:border-ink-700 dark:bg-ink-900"
        />
        {isOverride && (
          <button
            type="button"
            onClick={() => onChange(null)}
            className="text-xs text-ink-500 hover:underline dark:text-ink-400"
          >
            Reset to default ({effective})
          </button>
        )}
      </div>
    </div>
  );
}

function FieldIdRow({
  label,
  help,
  value,
  placeholder,
  onChange,
}: {
  label: string;
  help: string;
  value: string;
  placeholder: string;
  onChange: (v: string) => void;
}) {
  return (
    <div>
      <div className="flex items-baseline justify-between">
        <p className="text-sm font-medium">{label}</p>
        <span className="text-xs text-ink-500">
          {value ? "override" : "default chain"}
        </span>
      </div>
      <p className="mt-1 text-xs text-ink-500 dark:text-ink-400">{help}</p>
      <div className="mt-2 flex items-center gap-2">
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          className="w-64 rounded border border-ink-200 bg-white px-2 py-1 text-sm font-mono dark:border-ink-700 dark:bg-ink-900"
        />
      </div>
    </div>
  );
}
