// ADR-0043: Work Schedule configuration in Settings.
//
// Minimal viable configurator: name, timezone, working days (Mon-Sun
// bitmask), work_start_time, work_end_time, enabled toggle. Holidays are
// edited as a comma-separated string of YYYY-MM-DD values (v1 — date-picker
// upgrade tracked as a follow-up).
//
// Save flow: PUT /api/forge/schedule/activate. The Forge frontend then
// invokes the `startRecompute` resolver to push a task onto the queue.
// The dashboard banner reads /api/forge/schedule/status to render progress.

import { useEffect, useState } from "react";
import { invoke } from "@forge/bridge";
import { api } from "../lib/requestRemote";
import type { RecomputeStatus, WorkSchedule, WorkScheduleIn } from "../lib/types";

const DAY_BITS: Array<{ label: string; bit: number }> = [
  { label: "Mon", bit: 1 },
  { label: "Tue", bit: 2 },
  { label: "Wed", bit: 4 },
  { label: "Thu", bit: 8 },
  { label: "Fri", bit: 16 },
  { label: "Sat", bit: 32 },
  { label: "Sun", bit: 64 },
];

const DEFAULT_DRAFT: WorkScheduleIn = {
  name: "Default schedule",
  timezone: "UTC",
  working_days_mask: 31, // Mon-Fri
  work_start_time: "09:00",
  work_end_time: "17:00",
  holidays: [],
  enabled: true,
};

const COMMON_TIMEZONES = [
  "UTC",
  "America/Los_Angeles",
  "America/Denver",
  "America/Chicago",
  "America/New_York",
  "America/Mexico_City",
  "America/Sao_Paulo",
  "Europe/London",
  "Europe/Madrid",
  "Europe/Berlin",
  "Africa/Johannesburg",
  "Asia/Dubai",
  "Asia/Kolkata",
  "Asia/Singapore",
  "Asia/Tokyo",
  "Australia/Sydney",
];

export function WorkSchedulePanel() {
  const [draft, setDraft] = useState<WorkScheduleIn>(DEFAULT_DRAFT);
  const [loaded, setLoaded] = useState<WorkSchedule | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  useEffect(() => {
    api.getWorkSchedule().then(
      (s) => {
        setLoaded(s);
        if (s) {
          setDraft({
            name: s.name,
            timezone: s.timezone,
            working_days_mask: s.working_days_mask,
            work_start_time: s.work_start_time.slice(0, 5),
            work_end_time: s.work_end_time.slice(0, 5),
            holidays: s.holidays,
            enabled: s.enabled,
          });
        }
      },
      (e) => setError((e as Error).message),
    );
  }, []);

  const toggleDay = (bit: number) => {
    setDraft({
      ...draft,
      working_days_mask: draft.working_days_mask ^ bit,
    });
  };

  const save = async () => {
    setBusy(true);
    setError(null);
    setSaved(null);
    try {
      await api.putWorkSchedule(draft);
      // After backend persists + sets recompute_status='pending', kick the
      // Forge consumer queue. Failure here is non-fatal — the next /status
      // poll will show "pending" and the user can retry.
      try {
        await invoke("startRecompute");
      } catch {
        // surfaced via status polling rather than a hard error here
      }
      setSaved("Saved. Recomputing metrics under the new schedule…");
      setLoaded(await api.getWorkSchedule());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (loaded === undefined) {
    return (
      <section className="rounded-md border border-ink-200 bg-white p-4 text-sm dark:border-ink-800 dark:bg-ink-900">
        Loading work schedule…
      </section>
    );
  }

  return (
    <section className="space-y-4 rounded-md border border-ink-200 bg-white p-4 text-sm dark:border-ink-800 dark:bg-ink-900">
      <div>
        <h3 className="text-base font-semibold text-ink-900 dark:text-ink-100">
          Work schedule
        </h3>
        <p className="mt-1 text-xs text-ink-500 dark:text-ink-400">
          By default, Jira Flow Intelligence counts every hour as working time. Configure
          a work schedule to exclude weekends, evenings, and holidays from your
          flow metrics. This affects cycle-time alerts, time-in-status
          calculations, and the time signal in the bottleneck card. Saving
          re-runs every historical slice under the new math so charts and
          alerts stay consistent. (ADR-0043)
        </p>
      </div>

      <Row label="Schedule name">
        <input
          type="text"
          value={draft.name}
          onChange={(e) => setDraft({ ...draft, name: e.target.value })}
          className="w-full rounded border border-ink-300 bg-white px-2 py-1 dark:border-ink-700 dark:bg-ink-950"
        />
      </Row>

      <Row label="Timezone">
        <select
          value={draft.timezone}
          onChange={(e) => setDraft({ ...draft, timezone: e.target.value })}
          className="w-full rounded border border-ink-300 bg-white px-2 py-1 dark:border-ink-700 dark:bg-ink-950"
        >
          {COMMON_TIMEZONES.map((tz) => (
            <option key={tz} value={tz}>
              {tz}
            </option>
          ))}
        </select>
      </Row>

      <Row label="Working days">
        <div className="flex flex-wrap gap-1">
          {DAY_BITS.map(({ label, bit }) => {
            const active = (draft.working_days_mask & bit) !== 0;
            return (
              <button
                key={bit}
                type="button"
                onClick={() => toggleDay(bit)}
                aria-pressed={active}
                className={
                  active
                    ? "rounded bg-accent px-2 py-1 text-xs font-medium text-white"
                    : "rounded border border-ink-300 px-2 py-1 text-xs text-ink-700 dark:border-ink-700 dark:text-ink-200"
                }
              >
                {label}
              </button>
            );
          })}
        </div>
      </Row>

      <Row label="Work hours">
        <div className="flex items-center gap-2">
          <input
            type="time"
            value={draft.work_start_time}
            onChange={(e) => setDraft({ ...draft, work_start_time: e.target.value })}
            className="rounded border border-ink-300 bg-white px-2 py-1 dark:border-ink-700 dark:bg-ink-950"
          />
          <span className="text-ink-500">to</span>
          <input
            type="time"
            value={draft.work_end_time}
            onChange={(e) => setDraft({ ...draft, work_end_time: e.target.value })}
            className="rounded border border-ink-300 bg-white px-2 py-1 dark:border-ink-700 dark:bg-ink-950"
          />
        </div>
      </Row>

      <Row label="Holidays (YYYY-MM-DD, comma-separated)">
        <input
          type="text"
          value={draft.holidays.join(", ")}
          onChange={(e) =>
            setDraft({
              ...draft,
              holidays: e.target.value
                .split(",")
                .map((s) => s.trim())
                .filter((s) => s.length > 0),
            })
          }
          placeholder="2026-12-25, 2027-01-01"
          className="w-full rounded border border-ink-300 bg-white px-2 py-1 dark:border-ink-700 dark:bg-ink-950"
        />
      </Row>

      <Row label="Enabled">
        <label className="inline-flex items-center gap-2">
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })}
          />
          <span className="text-xs text-ink-600 dark:text-ink-300">
            Apply this schedule to all duration calculations. Disable to revert
            to 24/7 calendar time.
          </span>
        </label>
      </Row>

      <div className="flex items-center gap-3 pt-2">
        <button
          type="button"
          onClick={save}
          disabled={busy}
          className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-accent-dark disabled:opacity-50"
        >
          {busy ? "Saving…" : "Save and recompute"}
        </button>
        {saved && <span className="text-xs text-green-700 dark:text-green-300">{saved}</span>}
        {error && (
          <span className="text-xs text-red-700 dark:text-red-300" role="alert">
            {error}
          </span>
        )}
      </div>
    </section>
  );
}

function Row({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <p className="text-xs font-medium text-ink-700 dark:text-ink-200">{label}</p>
      {children}
    </div>
  );
}

// Dashboard banner — non-dismissible while recompute is in flight.
export function RecomputeBanner() {
  const [status, setStatus] = useState<RecomputeStatus | null>(null);

  useEffect(() => {
    const poll = async () => {
      try {
        const s = await api.getRecomputeStatus();
        setStatus(s);
        return s;
      } catch {
        return null;
      }
    };
    poll();
    const t = setInterval(async () => {
      const s = await poll();
      if (s && (s.status === "completed" || s.status === "failed")) {
        clearInterval(t);
      }
    }, 5000);
    return () => clearInterval(t);
  }, []);

  if (!status) return null;
  if (status.status === "idle" || status.status === "completed") return null;

  if (status.status === "failed") {
    return (
      <div
        className="mb-3 rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-900 dark:border-red-700 dark:bg-red-950 dark:text-red-200"
        role="alert"
      >
        Recompute failed: {status.error ?? "unknown error"}.{" "}
        <span className="text-xs">Open Settings → Work schedule and re-save to retry.</span>
      </div>
    );
  }

  return (
    <div
      className="mb-3 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200"
      role="note"
    >
      <span className="font-medium">Recomputing metrics</span> with your new work
      schedule… <span className="font-mono text-xs">{status.progress_pct}%</span>{" "}
      complete ({status.rows_processed.toLocaleString()} of{" "}
      {status.total_rows.toLocaleString()} slices). Numbers may temporarily
      blend old and new math until this finishes.
    </div>
  );
}
