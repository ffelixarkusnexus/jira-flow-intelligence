// In-product alerts list (Overview tab).
//
// Pure rendering layer. The grouping logic, body reconstruction, and
// duration humanization all live in `../lib/` so they can be unit-tested
// directly (see ADR-0039). This component is the visual presentation +
// the layout/Tailwind concerns; it imports the lib for behavior.

import type { AlertOut } from "../lib/types";
import {
  groupAlerts,
  groupHeader,
  isPerTicketRule,
  renderAlertBody,
} from "../lib/alertGrouping";
import { relativeTime } from "../lib/duration";

const typeColors: Record<string, string> = {
  status_duration: "bg-orange-500",
  cycle_time: "bg-red-500",
  no_activity: "bg-amber-500",
  trend: "bg-purple-500",
  wip_breach: "bg-pink-500",
};

export function AlertsList({ alerts }: { alerts: AlertOut[] }) {
  if (!alerts.length) {
    return (
      <section className="rounded-2xl border border-ink-200 bg-white p-6 shadow-sm dark:border-ink-800 dark:bg-ink-900">
        <p className="text-sm uppercase tracking-wide text-ink-400">Alerts</p>
        <p className="mt-2 text-ink-600 dark:text-ink-400">
          No active alerts. Everything within thresholds.
        </p>
      </section>
    );
  }

  const groups = groupAlerts(alerts);

  return (
    <section className="rounded-2xl border border-ink-200 bg-white p-6 shadow-sm dark:border-ink-800 dark:bg-ink-900">
      <header className="flex items-center justify-between">
        <p className="text-sm uppercase tracking-wide text-ink-400">
          Alerts ({alerts.length})
        </p>
      </header>
      <ul className="mt-4 divide-y divide-ink-100 dark:divide-ink-800">
        {groups.map((group) => {
          const showSubList =
            isPerTicketRule(group.ruleType) && group.alerts.length > 1;
          return (
            <li
              key={`${group.ruleId}-${group.newestTriggeredAt}`}
              className="flex items-start gap-3 py-3"
            >
              <span
                className={`mt-1.5 inline-block h-2 w-2 shrink-0 rounded-full ${
                  typeColors[group.ruleType] ?? "bg-ink-400"
                }`}
              />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">{groupHeader(group)}</p>
                <p className="mt-1 text-xs text-ink-400">
                  {group.ruleId} · {relativeTime(group.newestTriggeredAt)}
                </p>
                {showSubList && (
                  <ul className="mt-2 space-y-1 text-xs text-ink-600 dark:text-ink-400">
                    {group.alerts.map((a) => (
                      <li key={a.id} className="truncate">
                        {renderAlertBody(a)}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
