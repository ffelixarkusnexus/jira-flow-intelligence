// ADR-0044: per-issue read-only panel surfacing time-per-status data on
// the Jira issue view. Read-only — reuses the time_slices the changelog
// ingestion has already computed, surfaces the ADR-0042 external-blocking
// marker, deep-links into the project dashboard.

import { useEffect, useState } from "react";
import { invoke } from "@forge/bridge";

interface PanelSlice {
  status: string;
  entered_at: string;
  exited_at: string | null;
  duration_seconds: number;
  is_external_blocking: boolean;
}

interface PanelData {
  issue_key: string;
  current_status: string;
  status_history: PanelSlice[];
  total_cycle_time_seconds: number;
  is_in_current_bottleneck: boolean;
  project_dashboard_url: string;
}

interface PanelError {
  error: string;
  message: string;
  status?: number;
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  if (seconds < 86_400) {
    const h = Math.floor(seconds / 3600);
    const m = Math.round((seconds % 3600) / 60);
    return m === 0 ? `${h}h` : `${h}h ${m}m`;
  }
  const d = Math.floor(seconds / 86_400);
  const h = Math.round((seconds % 86_400) / 3600);
  return h === 0 ? `${d}d` : `${d}d ${h}h`;
}

function formatEnteredAt(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function IssuePanel() {
  const [data, setData] = useState<PanelData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    invoke<PanelData | PanelError>("getIssueData")
      .then((res) => {
        if ("error" in res) {
          setError(res.message);
        } else {
          setData(res);
        }
      })
      .catch((e) => setError((e as Error).message));
  }, []);

  if (error) {
    return (
      <div className="p-3 text-xs text-red-700 dark:text-red-300" role="alert">
        Could not load Jira Flow Intelligence panel: {error}
      </div>
    );
  }
  if (!data) {
    return (
      <div className="p-3 text-xs text-ink-500 dark:text-ink-400">
        Loading time-per-status…
      </div>
    );
  }

  return (
    <div className="space-y-3 p-3 text-sm">
      <div className="flex items-baseline justify-between gap-3">
        <p className="text-ink-700 dark:text-ink-200">
          <span className="font-medium">Time in status:</span>{" "}
          {formatDuration(data.total_cycle_time_seconds)}
        </p>
        <a
          href={data.project_dashboard_url}
          className="text-xs text-accent hover:underline"
          target="_top"
          rel="noopener noreferrer"
        >
          Open in Jira Flow Intelligence →
        </a>
      </div>

      {data.is_in_current_bottleneck && (
        <div
          className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200"
          role="note"
        >
          This ticket is in the project&apos;s currently named bottleneck:{" "}
          <span className="font-medium">{data.current_status}</span>.
        </div>
      )}

      <div className="overflow-hidden rounded-md border border-ink-200 dark:border-ink-800">
        <table className="w-full text-xs">
          <thead className="bg-ink-50 text-left dark:bg-ink-900">
            <tr>
              <th className="px-2 py-1 font-medium text-ink-600 dark:text-ink-400">
                Entered
              </th>
              <th className="px-2 py-1 font-medium text-ink-600 dark:text-ink-400">
                Status
              </th>
              <th className="px-2 py-1 text-right font-medium text-ink-600 dark:text-ink-400">
                Duration
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-100 dark:divide-ink-800">
            {data.status_history.map((s, i) => (
              <tr key={`${s.entered_at}-${i}`}>
                <td className="px-2 py-1 text-ink-600 dark:text-ink-300">
                  {formatEnteredAt(s.entered_at)}
                </td>
                <td className="px-2 py-1 text-ink-700 dark:text-ink-200">
                  <span>{s.status}</span>
                  {s.is_external_blocking && (
                    <span
                      className="ml-2 inline-flex items-center rounded-full bg-ink-100 px-2 py-0.5 text-[10px] font-medium text-ink-600 dark:bg-ink-800 dark:text-ink-300"
                      title="External-blocking status (ADR-0042). Time is tracked but excluded from bottleneck attribution."
                    >
                      external-blocking
                    </span>
                  )}
                  {s.exited_at === null && (
                    <span className="ml-2 inline-flex items-center rounded-full bg-accent/10 px-2 py-0.5 text-[10px] font-medium text-accent">
                      current
                    </span>
                  )}
                </td>
                <td className="px-2 py-1 text-right text-ink-700 dark:text-ink-200">
                  {formatDuration(s.duration_seconds)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
