// Pure alert-grouping + body-reconstruction logic for the in-product
// alerts list. Extracted from AlertsList.tsx so the grouping rules can
// be unit-tested directly without spinning up jsdom + RTL. See ADR-0039.
//
// Two UX rules locked here:
//
// 1. Per-ticket rules (status_duration, cycle_time, no_activity) group
//    by `rule_id` — one header per rule with a ticket count, the
//    sub-list of tickets underneath. The earlier flat 50-row dump
//    buried the structure of "22 tickets exceeded the same cycle
//    threshold".
//
// 2. Aggregate rules (trend, wip_breach) fire once per evaluation, not
//    per ticket — each fire is a semantically distinct signal. Grouping
//    them by rule_id would merge "cycle_time worsening +58%" with
//    "cycle_time worsening +24%" into one misleading row. They stay as
//    individual entries.
//
// Bodies for per-ticket alerts are reconstructed from structured payload
// fields (`elapsed_seconds`, `idle_seconds`, `duration_seconds`,
// `threshold_seconds`) so existing DB rows written before the backend's
// `human_duration` rollout also render with humanized durations, not
// stuck on whatever was baked into `payload.message` at fire time.
// Aggregate rules trust `payload.message` because the backend never
// emitted raw seconds for those rule types.

import type { AlertOut } from "./types";
import { humanDuration } from "./duration";

export const PER_TICKET_RULES = new Set([
  "status_duration",
  "cycle_time",
  "no_activity",
]);

export function isPerTicketRule(ruleType: string): boolean {
  return PER_TICKET_RULES.has(ruleType);
}

export function renderAlertBody(a: AlertOut): string {
  const p = (a.payload ?? {}) as Record<string, unknown>;
  const key = (p.issue_key as string) ?? a.issue_id ?? "";
  switch (a.rule_type) {
    case "status_duration": {
      const status = (p.status as string) ?? "";
      const dur = humanDuration(p.duration_seconds as number);
      const thr = humanDuration(p.threshold_seconds as number);
      return `${key} has been in ${status} for ${dur} (threshold ${thr}).`;
    }
    case "cycle_time": {
      const dur = humanDuration(p.elapsed_seconds as number);
      const thr = humanDuration(p.threshold_seconds as number);
      return `${key} exceeded cycle time threshold of ${thr} (elapsed ${dur}).`;
    }
    case "no_activity": {
      const dur = humanDuration(p.idle_seconds as number);
      const thr = humanDuration(p.threshold_seconds as number);
      return `${key} has had no activity for ${dur} (threshold ${thr}).`;
    }
    default:
      // trend, wip_breach, and any future aggregate rule type: trust the
      // backend's pre-formatted message (those don't carry raw seconds).
      return (p.message as string) ?? `${a.rule_id} triggered`;
  }
}

export interface AlertGroup {
  ruleId: string;
  ruleType: string;
  alerts: AlertOut[]; // sorted most-recent-first
  newestTriggeredAt: string;
}

export function groupAlerts(alerts: AlertOut[]): AlertGroup[] {
  // Per-ticket alerts share a bucket per rule_id; aggregate alerts get a
  // synthetic singleton key per alert so each fire stays distinct.
  const buckets = new Map<string, AlertOut[]>();
  for (const a of alerts) {
    const key = isPerTicketRule(a.rule_type) ? `r:${a.rule_id}` : `a:${a.id}`;
    const list = buckets.get(key);
    if (list) {
      list.push(a);
    } else {
      buckets.set(key, [a]);
    }
  }
  const groups: AlertGroup[] = [];
  for (const list of buckets.values()) {
    const sorted = [...list].sort((x, y) =>
      y.triggered_at.localeCompare(x.triggered_at),
    );
    groups.push({
      ruleId: sorted[0].rule_id,
      ruleType: sorted[0].rule_type,
      alerts: sorted,
      newestTriggeredAt: sorted[0].triggered_at,
    });
  }
  // Most-recent group at the top.
  groups.sort((a, b) =>
    b.newestTriggeredAt.localeCompare(a.newestTriggeredAt),
  );
  return groups;
}

export function groupHeader(group: AlertGroup): string {
  const count = group.alerts.length;
  if (!isPerTicketRule(group.ruleType) || count === 1) {
    return renderAlertBody(group.alerts[0]);
  }
  switch (group.ruleType) {
    case "status_duration":
      return `${count} tickets exceeded the in-status duration threshold`;
    case "cycle_time":
      return `${count} tickets exceeded the cycle-time threshold`;
    case "no_activity":
      return `${count} tickets had no activity past the threshold`;
    default:
      return `${count} alerts for rule ${group.ruleId}`;
  }
}
