// Alert rule management.
//
// Surfaces the four alert rule types (status_duration, cycle_time,
// no_activity, trend, wip_breach) as a list with inline create / edit
// / disable / delete. Uses the same Settings-tab pattern WIP limits
// and Backfill use — per-row PUT on save, instant feedback.
//
// `wip_breach` is also editable here; its config (status, project_key,
// sustained_minutes) overlaps with the WIP-limits panel but the panel
// shows the full picture in one place. Editing breach_minutes still
// happens on the wip_limits row (same field).

import { useEffect, useState } from "react";

import { api } from "../lib/requestRemote";
import type { AlertDestination, AlertRule, AlertRuleType } from "../lib/types";

interface Props {
  knownStatuses: string[];
}

interface RuleTemplate {
  type: AlertRuleType;
  label: string;
  description: string;
  defaultConfig: Record<string, unknown>;
  fields: ConfigField[];
}

interface ConfigField {
  key: string;
  label: string;
  kind: "number" | "text" | "status" | "metric" | "direction" | "duration_seconds";
  unit?: string;
  hint?: string;
}

// Each template = one rule type's UX. Fields drive the inline form.
// Backend validates — frontend is best-effort.
const TEMPLATES: RuleTemplate[] = [
  {
    type: "status_duration",
    label: "Ticket stuck in status",
    description:
      "Fires when a ticket sits in the watched status longer than the threshold. Per-ticket; one alert per ticket-status entry.",
    defaultConfig: { status: "", threshold_seconds: 24 * 3600 },
    fields: [
      { key: "status", label: "Status", kind: "status" },
      { key: "threshold_seconds", label: "Threshold", kind: "duration_seconds" },
    ],
  },
  {
    type: "cycle_time",
    label: "Cycle time exceeded",
    description:
      "Fires when a ticket's total cycle time (created → done, or created → now if still open) crosses the threshold.",
    defaultConfig: { threshold_seconds: 14 * 86400 },
    fields: [{ key: "threshold_seconds", label: "Threshold", kind: "duration_seconds" }],
  },
  {
    type: "no_activity",
    label: "No activity",
    description:
      "Fires on in-flight tickets with no transitions or updates for the threshold period.",
    defaultConfig: { threshold_seconds: 7 * 86400 },
    fields: [{ key: "threshold_seconds", label: "Idle threshold", kind: "duration_seconds" }],
  },
  {
    type: "trend",
    label: "Trend worsening",
    description:
      "Fires when a metric (cycle time, throughput, WIP) shifts beyond the threshold percentage vs. the prior window.",
    defaultConfig: {
      metric: "cycle_time",
      direction: "worsening",
      threshold_pct: 30,
    },
    fields: [
      { key: "metric", label: "Metric", kind: "metric" },
      { key: "direction", label: "Direction", kind: "direction" },
      {
        key: "threshold_pct",
        label: "Change threshold",
        kind: "number",
        unit: "%",
        hint: "30 = 30% movement vs prior window",
      },
    ],
  },
  {
    type: "wip_breach",
    label: "WIP breach",
    description:
      "Fires when WIP in the watched status exceeds its configured limit (set in the WIP limits panel above). breach_minutes on the WIP-limit row gates how long a breach must persist before firing.",
    defaultConfig: { status: "" },
    fields: [{ key: "status", label: "Status", kind: "status" }],
  },
];

function templateFor(type: AlertRuleType): RuleTemplate | undefined {
  return TEMPLATES.find((t) => t.type === type);
}

export function AlertRulesPanel({ knownStatuses }: Props) {
  const [rules, setRules] = useState<AlertRule[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState<AlertRule | null>(null);
  const [busy, setBusy] = useState(false);
  // ADR-0037 per-rule destination override. `destinations` is the tenant's
  // available delivery destinations; `selectedDestIds` is the set bound to the
  // rule currently being edited (empty = fall back to tenant defaults).
  const [destinations, setDestinations] = useState<AlertDestination[]>([]);
  const [selectedDestIds, setSelectedDestIds] = useState<string[]>([]);

  useEffect(() => {
    api.getAlertDestinations().then(
      (r) => setDestinations(r.destinations),
      () => setDestinations([]),
    );
  }, []);

  const refresh = () => {
    api.getAlertRules().then(
      (r) => setRules(r),
      (e) => setError((e as Error).message),
    );
  };

  useEffect(refresh, []);

  const startCreate = (template: RuleTemplate) => {
    const id = `${template.type}-${Date.now().toString(36)}`;
    setDraft({
      id,
      type: template.type,
      enabled: true,
      config: { ...template.defaultConfig },
    });
    setEditingId(id);
    setSelectedDestIds([]);
    setError(null);
  };

  const startEdit = (rule: AlertRule) => {
    setDraft({ ...rule, config: { ...rule.config } });
    setEditingId(rule.id);
    setSelectedDestIds([]);
    api.getRuleDestinations(rule.id).then(
      (r) => setSelectedDestIds(r.destination_ids),
      () => setSelectedDestIds([]),
    );
    setError(null);
  };

  const toggleDest = (id: string) => {
    setSelectedDestIds((prev) =>
      prev.includes(id) ? prev.filter((d) => d !== id) : [...prev, id],
    );
  };

  const cancelEdit = () => {
    setDraft(null);
    setEditingId(null);
  };

  const save = async () => {
    if (!draft) return;
    setBusy(true);
    setError(null);
    try {
      await api.putAlertRule(draft);
      await api.putRuleDestinations(draft.id, { destination_ids: selectedDestIds });
      cancelEdit();
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const toggleEnabled = async (rule: AlertRule) => {
    setBusy(true);
    setError(null);
    try {
      await api.putAlertRule({ ...rule, enabled: !rule.enabled });
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (ruleId: string) => {
    setBusy(true);
    setError(null);
    try {
      await api.deleteAlertRule(ruleId);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const updateDraftConfig = (key: string, value: unknown) => {
    if (!draft) return;
    setDraft({ ...draft, config: { ...draft.config, [key]: value } });
  };

  return (
    <section className="rounded-2xl border border-ink-200 bg-white p-6 shadow-sm dark:border-ink-800 dark:bg-ink-900">
      <header className="mb-4">
        <p className="text-sm uppercase tracking-wide text-ink-400">Settings</p>
        <h3 className="mt-1 text-xl font-semibold">Alert rules</h3>
        <p className="mt-1 text-xs text-ink-500 dark:text-ink-400">
          Rules are evaluated server-side on a schedule (and on ticket changes for
          short-threshold rules). Triggered alerts appear on the Overview tab and are
          pushed to any delivery destinations you set above. Disable a rule to keep it without
          firing; delete to remove.
        </p>
      </header>

      {error && (
        <div className="mb-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-900 dark:border-red-900/50 dark:bg-red-900/20 dark:text-red-100">
          {error}
        </div>
      )}

      {rules === null && <p className="text-sm text-ink-500">Loading…</p>}
      {rules?.length === 0 && (
        <p className="text-sm text-ink-500">
          No rules configured. Add one from the templates below.
        </p>
      )}

      {rules && rules.length > 0 && (
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase tracking-wide text-ink-400">
            <tr>
              <th className="py-2">ID</th>
              <th>Type</th>
              <th>Config</th>
              <th>Enabled</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rules.map((rule) => (
              <tr key={rule.id} className="border-t border-ink-100 dark:border-ink-800">
                <td className="py-2 font-mono text-xs">{rule.id}</td>
                <td className="text-xs">{templateFor(rule.type)?.label ?? rule.type}</td>
                <td className="text-xs text-ink-600 dark:text-ink-400">
                  <code className="font-mono">{JSON.stringify(rule.config)}</code>
                </td>
                <td>
                  <input
                    type="checkbox"
                    checked={rule.enabled}
                    onChange={() => toggleEnabled(rule)}
                    disabled={busy}
                  />
                </td>
                <td className="text-right">
                  <button
                    type="button"
                    onClick={() => startEdit(rule)}
                    disabled={busy}
                    className="text-xs text-ink-600 hover:underline disabled:opacity-50 dark:text-ink-300"
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    onClick={() => remove(rule.id)}
                    disabled={busy}
                    className="ml-3 text-xs text-red-600 hover:underline disabled:opacity-50 dark:text-red-400"
                  >
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {draft && (
        <RuleEditor
          rule={draft}
          knownStatuses={knownStatuses}
          destinations={destinations}
          selectedDestIds={selectedDestIds}
          onToggleDest={toggleDest}
          onChange={(r) => setDraft(r)}
          onConfigChange={updateDraftConfig}
          onSave={save}
          onCancel={cancelEdit}
          busy={busy}
          isNew={!rules?.some((r) => r.id === editingId)}
        />
      )}

      {!draft && (
        <div className="mt-6">
          <h4 className="text-sm font-semibold">Add a rule from a template</h4>
          <div className="mt-2 grid gap-2 sm:grid-cols-2">
            {TEMPLATES.map((t) => (
              <button
                key={t.type}
                type="button"
                onClick={() => startCreate(t)}
                disabled={busy}
                className="rounded-md border border-ink-200 bg-white p-3 text-left text-xs hover:border-ink-400 disabled:opacity-50 dark:border-ink-700 dark:bg-ink-900 dark:hover:border-ink-500"
              >
                <p className="font-medium">{t.label}</p>
                <p className="mt-1 text-ink-500 dark:text-ink-400">{t.description}</p>
              </button>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function RuleEditor({
  rule,
  knownStatuses,
  destinations,
  selectedDestIds,
  onToggleDest,
  onChange,
  onConfigChange,
  onSave,
  onCancel,
  busy,
  isNew,
}: {
  rule: AlertRule;
  knownStatuses: string[];
  destinations: AlertDestination[];
  selectedDestIds: string[];
  onToggleDest: (id: string) => void;
  onChange: (r: AlertRule) => void;
  onConfigChange: (key: string, value: unknown) => void;
  onSave: () => void;
  onCancel: () => void;
  busy: boolean;
  isNew: boolean;
}) {
  const template = templateFor(rule.type);
  if (!template) return null;
  return (
    <div className="mt-6 rounded-md border border-ink-200 bg-ink-50 p-4 dark:border-ink-700 dark:bg-ink-900/40">
      <p className="text-sm font-semibold">
        {isNew ? "Add" : "Edit"} {template.label}
      </p>
      <p className="mt-1 text-xs text-ink-500 dark:text-ink-400">{template.description}</p>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <label className="flex flex-col text-xs">
          <span className="text-ink-500 dark:text-ink-400">ID</span>
          <input
            type="text"
            value={rule.id}
            onChange={(e) => onChange({ ...rule, id: e.target.value })}
            disabled={!isNew}
            className="mt-1 rounded border border-ink-200 bg-white px-2 py-1 text-sm font-mono disabled:opacity-60 dark:border-ink-700 dark:bg-ink-900"
          />
        </label>
        {template.fields.map((field) => (
          <FieldInput
            key={field.key}
            field={field}
            value={rule.config[field.key]}
            onChange={(v) => onConfigChange(field.key, v)}
            knownStatuses={knownStatuses}
          />
        ))}
      </div>
      <div className="mt-4">
        <span className="text-xs font-medium text-ink-600 dark:text-ink-300">
          Delivery destinations
        </span>
        <p className="mt-0.5 text-xs text-ink-400">
          Leave all unchecked to use the tenant-default destinations. Checking any makes
          this rule push only to those.
        </p>
        {destinations.length === 0 ? (
          <p className="mt-1 text-xs text-ink-400">
            No destinations configured yet — add one in Alert delivery destinations above.
          </p>
        ) : (
          <div className="mt-2 flex flex-wrap gap-3">
            {destinations.map((d) => (
              <label key={d.id} className="flex items-center gap-1.5 text-xs">
                <input
                  type="checkbox"
                  checked={selectedDestIds.includes(d.id)}
                  onChange={() => onToggleDest(d.id)}
                />
                <span className="text-ink-600 dark:text-ink-300">
                  {d.name} <span className="text-ink-400">({d.type})</span>
                </span>
              </label>
            ))}
          </div>
        )}
      </div>
      <div className="mt-4 flex items-center gap-3">
        <button
          type="button"
          onClick={onSave}
          disabled={busy}
          className="rounded-md bg-ink-900 px-3 py-1.5 text-xs font-medium text-white shadow-sm hover:bg-ink-700 disabled:opacity-50 dark:bg-white dark:text-ink-900 dark:hover:bg-ink-200"
        >
          {busy ? "Saving…" : "Save"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={busy}
          className="text-xs text-ink-600 hover:underline disabled:opacity-50 dark:text-ink-400"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

function FieldInput({
  field,
  value,
  onChange,
  knownStatuses,
}: {
  field: ConfigField;
  value: unknown;
  onChange: (v: unknown) => void;
  knownStatuses: string[];
}) {
  if (field.kind === "status") {
    return (
      <label className="flex flex-col text-xs">
        <span className="text-ink-500 dark:text-ink-400">{field.label}</span>
        <input
          list="alert-known-statuses"
          type="text"
          value={(value as string) ?? ""}
          onChange={(e) => onChange(e.target.value)}
          placeholder="Code Review"
          className="mt-1 rounded border border-ink-200 bg-white px-2 py-1 text-sm dark:border-ink-700 dark:bg-ink-900"
        />
        <datalist id="alert-known-statuses">
          {knownStatuses.map((s) => (
            <option key={s} value={s} />
          ))}
        </datalist>
        {field.hint && <span className="mt-1 text-ink-400">{field.hint}</span>}
      </label>
    );
  }
  if (field.kind === "metric") {
    return (
      <label className="flex flex-col text-xs">
        <span className="text-ink-500 dark:text-ink-400">{field.label}</span>
        <select
          value={(value as string) ?? "cycle_time"}
          onChange={(e) => onChange(e.target.value)}
          className="mt-1 rounded border border-ink-200 bg-white px-2 py-1 text-sm dark:border-ink-700 dark:bg-ink-900"
        >
          <option value="cycle_time">cycle_time</option>
          <option value="throughput">throughput</option>
          <option value="wip">wip</option>
          <option value="avg_seconds">avg_seconds</option>
        </select>
      </label>
    );
  }
  if (field.kind === "direction") {
    return (
      <label className="flex flex-col text-xs">
        <span className="text-ink-500 dark:text-ink-400">{field.label}</span>
        <select
          value={(value as string) ?? "worsening"}
          onChange={(e) => onChange(e.target.value)}
          className="mt-1 rounded border border-ink-200 bg-white px-2 py-1 text-sm dark:border-ink-700 dark:bg-ink-900"
        >
          <option value="worsening">worsening</option>
          <option value="improving">improving</option>
        </select>
      </label>
    );
  }
  if (field.kind === "number") {
    return (
      <label className="flex flex-col text-xs">
        <span className="text-ink-500 dark:text-ink-400">
          {field.label}
          {field.unit ? ` (${field.unit})` : ""}
        </span>
        <input
          type="number"
          value={(value as number) ?? 0}
          onChange={(e) => onChange(Number.parseInt(e.target.value, 10))}
          className="mt-1 rounded border border-ink-200 bg-white px-2 py-1 text-sm dark:border-ink-700 dark:bg-ink-900"
        />
        {field.hint && <span className="mt-1 text-ink-400">{field.hint}</span>}
      </label>
    );
  }
  if (field.kind === "duration_seconds") {
    return <DurationFieldInput field={field} value={value} onChange={onChange} />;
  }
  // text fallback
  return (
    <label className="flex flex-col text-xs">
      <span className="text-ink-500 dark:text-ink-400">{field.label}</span>
      <input
        type="text"
        value={(value as string) ?? ""}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 rounded border border-ink-200 bg-white px-2 py-1 text-sm dark:border-ink-700 dark:bg-ink-900"
      />
      {field.hint && <span className="mt-1 text-ink-400">{field.hint}</span>}
    </label>
  );
}

type TimeUnit = "minutes" | "hours" | "days";
const UNIT_SECONDS: Record<TimeUnit, number> = {
  minutes: 60,
  hours: 3600,
  days: 86400,
};

// Largest unit that divides `seconds` cleanly. Falls back to minutes for
// values that aren't a whole number of hours or days — anything below a
// minute is rounded; sub-minute thresholds aren't meaningful for these alerts.
function pickLargestUnit(seconds: number): TimeUnit {
  if (seconds > 0 && seconds % 86400 === 0) return "days";
  if (seconds > 0 && seconds % 3600 === 0) return "hours";
  return "minutes";
}

function DurationFieldInput({
  field,
  value,
  onChange,
}: {
  field: ConfigField;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const seconds = (value as number) ?? 0;
  const [unit, setUnit] = useState<TimeUnit>(() => pickLargestUnit(seconds));

  // Re-sync the displayed unit when the underlying value changes from
  // outside (parent loaded a different rule). The guard avoids overwriting
  // the user's chosen unit while they're typing: if `seconds` matches what
  // we just emitted, the change came from us, so leave the unit alone.
  // Example of why this matters: with this guard, typing "24 hours" stays as
  // "24 hours" instead of snapping to "1 day" the moment seconds hits 86400.
  const localValue = (Math.floor(seconds / UNIT_SECONDS[unit]) || 0) * UNIT_SECONDS[unit];
  useEffect(() => {
    if (seconds !== localValue) {
      setUnit(pickLargestUnit(seconds));
    }
  }, [seconds, localValue]);

  const count =
    unit === "minutes"
      ? Math.max(0, Math.round(seconds / 60))
      : Math.max(0, Math.floor(seconds / UNIT_SECONDS[unit]));

  const setSeconds = (nextCount: number, nextUnit: TimeUnit) => {
    onChange(Math.max(0, nextCount) * UNIT_SECONDS[nextUnit]);
  };

  return (
    <label className="flex flex-col text-xs">
      <span className="text-ink-500 dark:text-ink-400">{field.label}</span>
      <div className="mt-1 flex gap-2">
        <input
          type="number"
          min={0}
          value={count}
          onChange={(e) => {
            const n = Number.parseInt(e.target.value, 10);
            setSeconds(Number.isFinite(n) ? n : 0, unit);
          }}
          className="w-24 rounded border border-ink-200 bg-white px-2 py-1 text-sm dark:border-ink-700 dark:bg-ink-900"
        />
        <select
          value={unit}
          onChange={(e) => {
            const u = e.target.value as TimeUnit;
            setUnit(u);
            setSeconds(count, u);
          }}
          className="rounded border border-ink-200 bg-white px-2 py-1 text-sm dark:border-ink-700 dark:bg-ink-900"
        >
          <option value="minutes">minutes</option>
          <option value="hours">hours</option>
          <option value="days">days</option>
        </select>
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {[1, 3, 7, 14, 30].map((d) => (
          <button
            key={d}
            type="button"
            onClick={() => {
              setUnit("days");
              setSeconds(d, "days");
            }}
            className="rounded border border-ink-200 px-2 py-0.5 text-xs text-ink-600 hover:border-ink-400 hover:text-ink-900 dark:border-ink-700 dark:text-ink-300 dark:hover:border-ink-500 dark:hover:text-ink-100"
          >
            {d}d
          </button>
        ))}
      </div>
    </label>
  );
}
