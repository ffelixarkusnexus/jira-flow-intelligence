import type { Bottleneck, MetricsResponse } from "../lib/types";
import { formatDuration, formatNumber, formatPercent } from "../lib/format";

function Stat({
  label,
  current,
  previous,
  changePct,
  priorLabel = "prior",
}: {
  label: string;
  current: string;
  previous: string;
  changePct: number | null;
  priorLabel?: string;
}) {
  const direction = changePct === null ? "" : changePct > 0 ? "text-red-500" : "text-emerald-500";
  return (
    <div className="rounded-xl border border-ink-200 bg-ink-50 p-4 dark:border-ink-800 dark:bg-ink-900/40">
      <p className="text-xs uppercase tracking-wide text-ink-400">{label}</p>
      <p className="mt-2 text-xl font-semibold">{current}</p>
      <p className="mt-1 text-xs text-ink-600 dark:text-ink-400">
        {priorLabel}: {previous}
        {changePct !== null && (
          <span className={`ml-2 ${direction}`}>{formatPercent(changePct, true)}</span>
        )}
      </p>
    </div>
  );
}

// WIP card: shows `current / limit` when configured, breach styling when
// over (per ADR-0022). When no limit exists, falls back to the placeholder
// pointing at the Settings tab.
function WipStat({ current, limit }: { current: number; limit: number | null }) {
  if (limit === null) {
    return (
      <div className="rounded-xl border border-dashed border-ink-300 bg-ink-50/40 p-4 dark:border-ink-700 dark:bg-ink-900/20">
        <p className="text-xs uppercase tracking-wide text-ink-400">WIP</p>
        <p className="mt-2 text-sm font-medium text-ink-700 dark:text-ink-300">
          Configure limit to enable
        </p>
        <p className="mt-1 text-xs text-ink-500 dark:text-ink-500">
          Open the Settings tab to set a limit for this stage.
        </p>
      </div>
    );
  }
  const overLimit = current > limit;
  const approaching = !overLimit && limit > 0 && current >= limit * 0.8;
  const palette = overLimit
    ? "border-red-300 bg-red-50/70 dark:border-red-900/60 dark:bg-red-900/20"
    : approaching
      ? "border-amber-300 bg-amber-50/70 dark:border-amber-900/60 dark:bg-amber-900/20"
      : "border-ink-200 bg-ink-50 dark:border-ink-800 dark:bg-ink-900/40";
  return (
    <div className={`rounded-xl border p-4 ${palette}`}>
      <p className="text-xs uppercase tracking-wide text-ink-400">WIP</p>
      <p className="mt-2 text-xl font-semibold">
        {formatNumber(current, 1)} <span className="text-ink-500">/ {limit}</span>
      </p>
      <p className="mt-1 text-xs text-ink-600 dark:text-ink-400">
        {overLimit
          ? `${Math.round((current / limit - 1) * 100)}% over limit`
          : approaching
            ? "Approaching limit"
            : "Within limit"}
      </p>
    </div>
  );
}

export function BottleneckPanel({
  bottleneck,
  metrics,
  priorLabel,
}: {
  bottleneck: Bottleneck | null;
  metrics: MetricsResponse | null;
  // When the user picks a sprint window, this becomes the sprint name
  // ("Sprint 41") so the bottleneck card frames the comparison as
  // "this sprint vs Sprint 41" instead of the generic "prior window."
  priorLabel?: string;
}) {
  if (!bottleneck) return null;

  const timeChange = bottleneck.time_ratio !== null ? (bottleneck.time_ratio - 1) * 100 : null;
  const throughputChange =
    bottleneck.throughput_delta !== null ? bottleneck.throughput_delta * 100 : null;

  return (
    <section className="rounded-2xl border border-ink-200 bg-white p-6 shadow-sm dark:border-ink-800 dark:bg-ink-900">
      <header className="flex items-baseline justify-between">
        <div>
          <p className="text-sm uppercase tracking-wide text-ink-400">Bottleneck breakdown</p>
          <h3 className="mt-1 text-xl font-semibold">{bottleneck.status}</h3>
        </div>
        <p className="text-sm text-ink-400">score {bottleneck.score}</p>
      </header>
      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <Stat
          label="Avg time"
          current={formatDuration(bottleneck.current_avg_seconds)}
          previous={formatDuration(bottleneck.previous_avg_seconds)}
          changePct={timeChange}
          priorLabel={priorLabel}
        />
        <WipStat current={bottleneck.current_wip} limit={bottleneck.wip_limit} />
        <Stat
          label="Throughput"
          current={`${bottleneck.current_throughput}`}
          previous={`${bottleneck.previous_throughput}`}
          changePct={throughputChange}
          priorLabel={priorLabel}
        />
      </div>

      {metrics && (
        <details className="mt-6 group">
          <summary className="cursor-pointer text-sm text-ink-600 hover:text-ink-900 dark:text-ink-400 dark:hover:text-ink-100">
            All stages this window
          </summary>
          <table className="mt-3 w-full text-sm">
            <thead className="text-left text-xs uppercase tracking-wide text-ink-400">
              <tr>
                <th className="py-2">Status</th>
                <th>Avg</th>
                <th>P50</th>
                <th>P90</th>
                <th>WIP</th>
                <th>Throughput</th>
              </tr>
            </thead>
            <tbody>
              {metrics.current.statuses.map((s) => {
                const overLimit = s.wip_limit !== null && s.wip_avg > s.wip_limit;
                return (
                  <tr
                    key={s.status}
                    className={`border-t border-ink-100 dark:border-ink-800 ${
                      overLimit ? "bg-red-50/40 dark:bg-red-900/10" : ""
                    }`}
                  >
                    <td className="py-2 font-medium">{s.status}</td>
                    <td>{formatDuration(s.avg_seconds)}</td>
                    <td>{formatDuration(s.p50_seconds)}</td>
                    <td>{formatDuration(s.p90_seconds)}</td>
                    <td>
                      {s.wip_limit === null ? (
                        <span className="text-ink-400">—</span>
                      ) : (
                        <span className={overLimit ? "font-semibold text-red-700 dark:text-red-300" : ""}>
                          {formatNumber(s.wip_avg, 1)} / {s.wip_limit}
                        </span>
                      )}
                    </td>
                    <td>{s.throughput}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </details>
      )}
    </section>
  );
}
