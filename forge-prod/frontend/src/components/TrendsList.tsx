import type { Trend } from "../lib/types";
import { formatDuration, formatNumber, formatPercent } from "../lib/format";

const directionStyles: Record<Trend["direction"], string> = {
  worsening: "text-red-500",
  improving: "text-emerald-500",
  stable: "text-ink-400",
};

// Percentages above this render as "+500%+" / "-500%+" with the raw value
// in the tooltip — ratios on small baselines spike to absurd numbers and
// the precise figure isn't informative beyond "very large." Per ux-fix-
// sprint item #6.
const PERCENT_DISPLAY_CAP = 500;

function isTimeMetric(metric: string): boolean {
  return metric === "cycle_time" || metric === "avg_time";
}

function formatValue(metric: string, value: number): string {
  if (isTimeMetric(metric)) return formatDuration(value);
  // Throughput is a count; WIP is an average. One decimal on both.
  return formatNumber(value, 1);
}

function formatCappedPercent(change_pct: number): { display: string; raw: string } {
  const raw = `${formatPercent(change_pct, true)} (exact)`;
  if (!Number.isFinite(change_pct)) return { display: "—", raw };
  if (change_pct > PERCENT_DISPLAY_CAP) return { display: `+${PERCENT_DISPLAY_CAP}%+`, raw };
  if (change_pct < -PERCENT_DISPLAY_CAP) return { display: `-${PERCENT_DISPLAY_CAP}%+`, raw };
  return { display: formatPercent(change_pct, true), raw };
}

export function TrendsList({ trends }: { trends: Trend[] }) {
  const significant = trends.filter((t) => t.direction !== "stable").slice(0, 8);
  if (!significant.length) {
    return null;
  }
  return (
    <section className="rounded-2xl border border-ink-200 bg-white p-6 shadow-sm dark:border-ink-800 dark:bg-ink-900">
      <p className="text-sm uppercase tracking-wide text-ink-400">
        Significant changes vs prior window
      </p>
      <ul className="mt-3 divide-y divide-ink-100 dark:divide-ink-800">
        {significant.map((t, i) => {
          const { display, raw } = formatCappedPercent(t.change_pct);
          const wasNow = `was ${formatValue(t.metric, t.previous_value)}, now ${formatValue(t.metric, t.current_value)}`;
          return (
            <li
              key={`${t.metric}-${t.status ?? "*"}-${i}`}
              className="flex flex-col gap-0.5 py-2 text-sm sm:flex-row sm:items-center sm:justify-between"
            >
              <span className="font-medium">
                {t.status ? `${t.status} · ` : ""}
                {t.metric}
                <span className="ml-2 text-xs font-normal text-ink-500 dark:text-ink-400">
                  {wasNow}
                </span>
              </span>
              <span className={directionStyles[t.direction]} title={raw}>
                {display} ({t.direction})
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
