// Render tests for AlertsList. Pure JSX-output focus — the grouping
// rules and body reconstruction are unit-tested in
// `src/lib/alertGrouping.test.ts`; these tests assert that the
// component renders the data the lib produces.

import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import type { AlertOut } from "../lib/types";
import { AlertsList } from "./AlertsList";

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

describe("AlertsList", () => {
  it("empty state renders the no-alerts copy", () => {
    render(<AlertsList alerts={[]} />);
    expect(
      screen.getByText(/no active alerts. everything within thresholds/i),
    ).toBeInTheDocument();
  });

  it("header shows the total raw count (not the group count)", () => {
    const alerts = Array.from({ length: 22 }, (_, i) =>
      alert({
        id: i,
        rule_type: "cycle_time",
        rule_id: "cycle-7d",
        payload: { issue_key: `T-${i}`, elapsed_seconds: 700000, threshold_seconds: 604800 },
      }),
    );
    render(<AlertsList alerts={alerts} />);
    expect(screen.getByText("Alerts (22)")).toBeInTheDocument();
  });

  it("collapses 22 cycle_time alerts into a single group header with sub-list", () => {
    const alerts = Array.from({ length: 22 }, (_, i) =>
      alert({
        id: i,
        rule_type: "cycle_time",
        rule_id: "cycle-7d",
        triggered_at: "2026-06-02T11:00:00Z",
        payload: { issue_key: `T-${i}`, elapsed_seconds: 700000, threshold_seconds: 604800 },
      }),
    );
    render(<AlertsList alerts={alerts} />);

    expect(
      screen.getByText("22 tickets exceeded the cycle-time threshold"),
    ).toBeInTheDocument();
    // Sub-list contains each ticket's individual humanized body.
    expect(screen.getByText(/T-0 exceeded cycle time/)).toBeInTheDocument();
    expect(screen.getByText(/T-21 exceeded cycle time/)).toBeInTheDocument();
  });

  it("ignores a stale payload.message with raw seconds — body reconstructs from structured fields", () => {
    // Regression for the 2026-06-02 bug: AlertsList used to render
    // payload.message verbatim, surfacing "604800s" to customers.
    const a = alert({
      rule_type: "cycle_time",
      rule_id: "cycle-7d",
      payload: {
        issue_key: "T-1",
        elapsed_seconds: 625910,
        threshold_seconds: 604800,
        message:
          "T-1 exceeded cycle time threshold of 604800s (elapsed 625910s).",
      },
    });
    render(<AlertsList alerts={[a]} />);
    expect(screen.queryByText(/604800s/)).not.toBeInTheDocument();
    expect(screen.queryByText(/625910s/)).not.toBeInTheDocument();
    expect(screen.getByText(/7 days/)).toBeInTheDocument();
  });

  it("keeps aggregate rules (trend, wip_breach) as individual rows, not merged", () => {
    const alerts = [
      alert({
        id: 1,
        rule_type: "trend",
        rule_id: "trend-cycle-worsening",
        triggered_at: "2026-06-01T21:00:00Z",
        payload: { message: "system cycle_time worsening +58% vs prior window." },
      }),
      alert({
        id: 2,
        rule_type: "trend",
        rule_id: "trend-cycle-worsening",
        triggered_at: "2026-06-01T11:00:00Z",
        payload: { message: "system cycle_time worsening +24% vs prior window." },
      }),
    ];
    render(<AlertsList alerts={alerts} />);
    expect(screen.getByText(/\+58%/)).toBeInTheDocument();
    expect(screen.getByText(/\+24%/)).toBeInTheDocument();
  });

  it("does not render a sub-list for singleton groups (avoid visual duplication)", () => {
    const a = alert({
      rule_type: "cycle_time",
      rule_id: "cycle-7d",
      payload: { issue_key: "T-1", elapsed_seconds: 700000, threshold_seconds: 604800 },
    });
    const { container } = render(<AlertsList alerts={[a]} />);
    // Header is the body itself when count === 1; only one render of the
    // ticket text, not two (header + sub-item).
    const matches = within(container).getAllByText(/T-1 exceeded cycle time/);
    expect(matches).toHaveLength(1);
  });

  it("renders rule_id and a relative-time suffix under each group header", () => {
    const a = alert({
      rule_type: "cycle_time",
      rule_id: "my-rule-id",
      payload: { issue_key: "T-1", elapsed_seconds: 700000, threshold_seconds: 604800 },
    });
    render(<AlertsList alerts={[a]} />);
    // rule_id appears under the group header next to the relative time
    expect(screen.getByText(/my-rule-id\s*·\s*\d+[mhd]\s*ago/)).toBeInTheDocument();
  });
});
