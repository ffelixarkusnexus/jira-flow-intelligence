// Locks the grouping + body-reconstruction rules for the in-product
// alerts list. The 2026-06-02 customer report ("50-row flat dump, raw
// seconds in bodies") is the regression these tests prevent.

import { describe, expect, it } from "vitest";
import type { AlertOut } from "./types";
import {
  groupAlerts,
  groupHeader,
  isPerTicketRule,
  renderAlertBody,
} from "./alertGrouping";

// Compact factory so each test reads as data, not boilerplate.
function alert(o: Partial<AlertOut> & Pick<AlertOut, "rule_type">): AlertOut {
  return {
    id: o.id ?? 1,
    rule_id: o.rule_id ?? "rule-1",
    rule_type: o.rule_type,
    issue_id: o.issue_id ?? null,
    status: o.status ?? null,
    triggered_at: o.triggered_at ?? "2026-06-02T12:00:00Z",
    payload: o.payload ?? {},
  };
}

describe("isPerTicketRule", () => {
  it.each(["status_duration", "cycle_time", "no_activity"])(
    "%s is a per-ticket rule",
    (rt) => {
      expect(isPerTicketRule(rt)).toBe(true);
    },
  );

  it.each(["trend", "wip_breach", "unknown"])(
    "%s is NOT a per-ticket rule (aggregate / fallback)",
    (rt) => {
      expect(isPerTicketRule(rt)).toBe(false);
    },
  );
});

describe("renderAlertBody — reconstructs body from structured fields", () => {
  it("status_duration uses payload.duration_seconds + threshold_seconds", () => {
    const a = alert({
      rule_type: "status_duration",
      payload: {
        issue_key: "ABC-1",
        status: "Code Review",
        duration_seconds: 90060, // 1d 1h 1m → "1 day, 1 hour"
        threshold_seconds: 86400, // 1d
      },
    });
    expect(renderAlertBody(a)).toBe(
      "ABC-1 has been in Code Review for 1 day, 1 hour (threshold 1 day).",
    );
  });

  it("cycle_time uses payload.elapsed_seconds + threshold_seconds", () => {
    const a = alert({
      rule_type: "cycle_time",
      payload: {
        issue_key: "DEMO-A-P15",
        elapsed_seconds: 625910, // 7 days, 5 hours
        threshold_seconds: 604800, // 7 days
      },
    });
    expect(renderAlertBody(a)).toBe(
      "DEMO-A-P15 exceeded cycle time threshold of 7 days (elapsed 7 days, 5 hours).",
    );
  });

  it("no_activity uses payload.idle_seconds + threshold_seconds", () => {
    const a = alert({
      rule_type: "no_activity",
      payload: {
        issue_key: "SCRUM-1",
        idle_seconds: 2385949, // 27 days, 14 hours
        threshold_seconds: 259200, // 3 days
      },
    });
    expect(renderAlertBody(a)).toBe(
      "SCRUM-1 has had no activity for 27 days, 14 hours (threshold 3 days).",
    );
  });

  it("IGNORES a stale payload.message that contains raw seconds for per-ticket rules", () => {
    // Regression test: the bug from 2026-06-02 was that AlertsList used
    // payload.message directly, which the backend had baked with raw
    // seconds. After the fix, the component must reconstruct from
    // structured fields and ignore the stale message string.
    const a = alert({
      rule_type: "cycle_time",
      payload: {
        issue_key: "DEMO-A-P15",
        elapsed_seconds: 625910,
        threshold_seconds: 604800,
        message:
          "DEMO-A-P15 exceeded cycle time threshold of 604800s (elapsed 625910s).",
      },
    });
    const body = renderAlertBody(a);
    expect(body).not.toMatch(/\d+s\b/); // no raw seconds
    expect(body).toContain("7 days");
  });

  it("trend rule falls through to payload.message (no raw seconds for aggregates)", () => {
    const a = alert({
      rule_type: "trend",
      payload: {
        message: "system cycle_time worsening +58% vs prior window.",
      },
    });
    expect(renderAlertBody(a)).toBe(
      "system cycle_time worsening +58% vs prior window.",
    );
  });

  it("wip_breach rule falls through to payload.message", () => {
    const a = alert({
      rule_type: "wip_breach",
      payload: { message: "WIP in Code Review = 13 / 8 (over limit)." },
    });
    expect(renderAlertBody(a)).toBe("WIP in Code Review = 13 / 8 (over limit).");
  });

  it("unknown rule_type gracefully falls back to '<rule_id> triggered'", () => {
    const a = alert({ rule_type: "unknown_rule", rule_id: "exotic-rule" });
    expect(renderAlertBody(a)).toBe("exotic-rule triggered");
  });

  it("uses issue_id as the key when payload.issue_key is missing", () => {
    const a = alert({
      rule_type: "cycle_time",
      issue_id: "10042",
      payload: { elapsed_seconds: 604800, threshold_seconds: 604800 },
    });
    expect(renderAlertBody(a)).toContain("10042 exceeded cycle time");
  });
});

describe("groupAlerts — per-ticket rules collapse by rule_id", () => {
  it("empty input → empty groups", () => {
    expect(groupAlerts([])).toEqual([]);
  });

  it("22 cycle_time alerts under same rule_id → ONE group with 22 alerts", () => {
    const alerts = Array.from({ length: 22 }, (_, i) =>
      alert({
        id: 100 + i,
        rule_type: "cycle_time",
        rule_id: "cycle-7d",
        triggered_at: `2026-06-02T${String(10 + (i % 12)).padStart(2, "0")}:00:00Z`,
        payload: { issue_key: `T-${i}`, elapsed_seconds: 700000 },
      }),
    );
    const groups = groupAlerts(alerts);
    expect(groups).toHaveLength(1);
    expect(groups[0].ruleId).toBe("cycle-7d");
    expect(groups[0].alerts).toHaveLength(22);
  });

  it("alerts within a group are sorted most-recent-first", () => {
    const alerts = [
      alert({
        id: 1,
        rule_type: "cycle_time",
        rule_id: "cycle-7d",
        triggered_at: "2026-06-01T08:00:00Z",
      }),
      alert({
        id: 2,
        rule_type: "cycle_time",
        rule_id: "cycle-7d",
        triggered_at: "2026-06-02T12:00:00Z",
      }),
      alert({
        id: 3,
        rule_type: "cycle_time",
        rule_id: "cycle-7d",
        triggered_at: "2026-05-30T14:00:00Z",
      }),
    ];
    const groups = groupAlerts(alerts);
    expect(groups[0].alerts.map((a) => a.id)).toEqual([2, 1, 3]);
    expect(groups[0].newestTriggeredAt).toBe("2026-06-02T12:00:00Z");
  });
});

describe("groupAlerts — aggregate rules stay as individual groups", () => {
  it("two trend fires for the SAME rule_id → TWO singleton groups, not one merged", () => {
    // The 2026-06-02 dashboard showed two trend-cycle-worsening fires:
    // +58% and +24%. They share rule_id but are semantically distinct
    // signals — grouping would lose the per-fire change_pct.
    const alerts = [
      alert({
        id: 1,
        rule_type: "trend",
        rule_id: "trend-cycle-worsening",
        triggered_at: "2026-06-01T21:00:00Z",
        payload: {
          message: "system cycle_time worsening +58% vs prior window.",
        },
      }),
      alert({
        id: 2,
        rule_type: "trend",
        rule_id: "trend-cycle-worsening",
        triggered_at: "2026-06-01T11:00:00Z",
        payload: {
          message: "system cycle_time worsening +24% vs prior window.",
        },
      }),
    ];
    const groups = groupAlerts(alerts);
    expect(groups).toHaveLength(2);
    expect(groups.map((g) => g.alerts.length)).toEqual([1, 1]);
    expect(groups[0].alerts[0].id).toBe(1); // newer trend at top
    expect(groups[1].alerts[0].id).toBe(2);
  });

  it("wip_breach fires stay distinct per fire", () => {
    const alerts = [
      alert({
        id: 1,
        rule_type: "wip_breach",
        rule_id: "wip-codereview",
        triggered_at: "2026-06-02T11:00:00Z",
      }),
      alert({
        id: 2,
        rule_type: "wip_breach",
        rule_id: "wip-codereview",
        triggered_at: "2026-06-02T10:00:00Z",
      }),
    ];
    expect(groupAlerts(alerts)).toHaveLength(2);
  });
});

describe("groupAlerts — mixed sets sort by newest member", () => {
  it("cycle_time group (newest 1h ago) ranks above trend group (newest 1d ago)", () => {
    const alerts = [
      alert({
        id: 1,
        rule_type: "trend",
        rule_id: "trend-cycle",
        triggered_at: "2026-06-01T12:00:00Z", // 1 day ago
        payload: { message: "trend message" },
      }),
      alert({
        id: 2,
        rule_type: "cycle_time",
        rule_id: "cycle-7d",
        triggered_at: "2026-06-02T11:00:00Z", // 1h ago
      }),
      alert({
        id: 3,
        rule_type: "cycle_time",
        rule_id: "cycle-7d",
        triggered_at: "2026-06-02T09:00:00Z", // 3h ago
      }),
    ];
    const groups = groupAlerts(alerts);
    expect(groups).toHaveLength(2); // cycle_time merged into 1, trend singleton
    expect(groups[0].ruleType).toBe("cycle_time");
    expect(groups[0].alerts).toHaveLength(2);
    expect(groups[1].ruleType).toBe("trend");
  });
});

describe("groupHeader", () => {
  it("singleton group (count=1) returns the body of the single alert", () => {
    const a = alert({
      rule_type: "cycle_time",
      rule_id: "cycle-7d",
      payload: {
        issue_key: "T-1",
        elapsed_seconds: 700000,
        threshold_seconds: 604800,
      },
    });
    const [group] = groupAlerts([a]);
    expect(groupHeader(group)).toContain("T-1 exceeded cycle time threshold");
  });

  it("multi-ticket cycle_time group shows count + canned header", () => {
    const alerts = Array.from({ length: 22 }, (_, i) =>
      alert({
        id: i,
        rule_type: "cycle_time",
        rule_id: "cycle-7d",
        triggered_at: "2026-06-02T12:00:00Z",
        payload: { issue_key: `T-${i}` },
      }),
    );
    const [group] = groupAlerts(alerts);
    expect(groupHeader(group)).toBe(
      "22 tickets exceeded the cycle-time threshold",
    );
  });

  it("multi-ticket no_activity group", () => {
    const alerts = Array.from({ length: 5 }, (_, i) =>
      alert({
        id: i,
        rule_type: "no_activity",
        rule_id: "noact-72h",
        triggered_at: "2026-06-02T12:00:00Z",
        payload: { issue_key: `T-${i}` },
      }),
    );
    const [group] = groupAlerts(alerts);
    expect(groupHeader(group)).toBe(
      "5 tickets had no activity past the threshold",
    );
  });

  it("multi-ticket status_duration group", () => {
    const alerts = Array.from({ length: 3 }, (_, i) =>
      alert({
        id: i,
        rule_type: "status_duration",
        rule_id: "stuck-codereview",
        triggered_at: "2026-06-02T12:00:00Z",
        payload: { issue_key: `T-${i}` },
      }),
    );
    const [group] = groupAlerts(alerts);
    expect(groupHeader(group)).toBe(
      "3 tickets exceeded the in-status duration threshold",
    );
  });

  it("aggregate rule (always count=1) uses the rendered body, never the canned header", () => {
    const a = alert({
      rule_type: "trend",
      rule_id: "trend-cycle-worsening",
      payload: {
        message: "system cycle_time worsening +58% vs prior window.",
      },
    });
    const [group] = groupAlerts([a]);
    expect(groupHeader(group)).toBe(
      "system cycle_time worsening +58% vs prior window.",
    );
  });
});
