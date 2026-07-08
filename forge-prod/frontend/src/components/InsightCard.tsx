import type { Bottleneck } from "../lib/types";
import { confidenceLabel } from "../lib/format";

const confidenceStyles: Record<Bottleneck["confidence"], string> = {
  medium: "bg-amber-100 text-amber-900 dark:bg-amber-900/30 dark:text-amber-100",
  high: "bg-orange-100 text-orange-900 dark:bg-orange-900/30 dark:text-orange-100",
  very_high: "bg-red-100 text-red-900 dark:bg-red-900/30 dark:text-red-100",
};

export function InsightCard({
  bottleneck,
  explanation,
  isSparse = false,
  windowLabel = "30d",
}: {
  bottleneck: Bottleneck | null;
  explanation: string | null;
  isSparse?: boolean;
  windowLabel?: string;
}) {
  if (!bottleneck) {
    // "Sparse" path — the window has zero completions and zero closed slices.
    // The bottleneck pipeline has no signal to reason from, so saying "flow
    // looks healthy" is misleading. Especially common on low-activity
    // projects when the user opens the plugin from a `jira:projectPage`.
    if (isSparse) {
      return (
        <section className="rounded-2xl border border-ink-200 bg-white p-8 shadow-sm dark:border-ink-800 dark:bg-ink-900">
          <p className="text-sm uppercase tracking-wide text-ink-400">Top insight</p>
          <h2 className="mt-2 text-2xl font-semibold">Not enough recent activity.</h2>
          <p className="mt-2 text-ink-600 dark:text-ink-400">
            No tickets completed or transitioned in the {windowLabel} window for this project.
            Widen the window above, or open the Flow tab to see what's currently in flight.
          </p>
        </section>
      );
    }
    return (
      <section className="rounded-2xl border border-ink-200 bg-white p-8 shadow-sm dark:border-ink-800 dark:bg-ink-900">
        <p className="text-sm uppercase tracking-wide text-ink-400">Top insight</p>
        <h2 className="mt-2 text-2xl font-semibold">Flow looks healthy.</h2>
        <p className="mt-2 text-ink-600 dark:text-ink-400">
          No stage shows a significant slowdown vs. the prior window.
        </p>
      </section>
    );
  }

  return (
    <section className="rounded-2xl border border-ink-200 bg-white p-8 shadow-sm dark:border-ink-800 dark:bg-ink-900">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-sm uppercase tracking-wide text-ink-400">Top insight</p>
          <h2 className="mt-2 text-2xl font-semibold">
            {bottleneck.status} is the current bottleneck.
          </h2>
        </div>
        <span
          className={`shrink-0 rounded-full px-3 py-1 text-xs font-medium uppercase ${
            confidenceStyles[bottleneck.confidence]
          }`}
        >
          {confidenceLabel(bottleneck.confidence)} confidence
        </span>
      </div>

      {explanation && (
        <p className="mt-4 text-lg leading-relaxed text-ink-800 dark:text-ink-100">{explanation}</p>
      )}

      <ul className="mt-6 grid gap-2 text-sm text-ink-600 dark:text-ink-400">
        {bottleneck.reasons.map((r) => (
          <li key={r} className="flex items-center gap-2">
            <span className="h-1.5 w-1.5 rounded-full bg-red-500" />
            {r}
          </li>
        ))}
      </ul>
    </section>
  );
}
