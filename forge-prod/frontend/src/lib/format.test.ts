// Tests for the chart-/tooltip-oriented formatters in format.ts.
//
// Note: this file's `formatDuration` is the COMPACT "7d 5h" style used
// in charts where space is scarce, distinct from `humanDuration` in
// `./duration.ts` which is the VERBOSE "7 days, 5 hours" form used in
// alert bodies. Both intentionally exist — see ADR-0039 for the gap
// note on whether these should be unified.

import { describe, expect, it } from "vitest";
import {
  confidenceLabel,
  formatDuration,
  formatNumber,
  formatPercent,
} from "./format";

describe("formatDuration", () => {
  it.each<[number, string]>([
    // Non-finite / non-positive → em-dash placeholder
    [0, "—"],
    [-1, "—"],
    [NaN, "—"],
    [Infinity, "—"],
    // Under an hour → "Nm"
    [60, "1m"],
    [3599, "59m"],
    // Hours dominant → "Nh Mm"
    [3600, "1h 0m"],
    [7320, "2h 2m"],
    // Days dominant → "Nd Mh" (minutes dropped)
    [86400, "1d 0h"],
    [90000, "1d 1h"],
    [604800, "7d 0h"],
    [625910, "7d 5h"],
  ])("formatDuration(%s) === %j", (input, expected) => {
    expect(formatDuration(input)).toBe(expected);
  });
});

describe("formatPercent", () => {
  it("rounds toward nearest integer and appends '%' by default", () => {
    expect(formatPercent(12.3)).toBe("12%");
    expect(formatPercent(12.6)).toBe("13%");
    expect(formatPercent(0)).toBe("0%");
    expect(formatPercent(-7.4)).toBe("-7%");
  });

  it("with signed=true adds '+' for positive but leaves '-' for negative (single sign)", () => {
    expect(formatPercent(58, true)).toBe("+58%");
    expect(formatPercent(0, true)).toBe("0%");
    expect(formatPercent(-24, true)).toBe("-24%");
  });

  it("non-finite values render as the em-dash placeholder", () => {
    expect(formatPercent(NaN)).toBe("—");
    expect(formatPercent(Infinity)).toBe("—");
  });
});

describe("formatNumber", () => {
  it("respects the digits parameter (defaults to 1)", () => {
    expect(formatNumber(3.14159)).toBe("3.1");
    expect(formatNumber(3.14159, 3)).toBe("3.142");
    expect(formatNumber(0, 0)).toBe("0");
    expect(formatNumber(-1.5, 2)).toBe("-1.50");
  });

  it("non-finite values render as em-dash placeholder", () => {
    expect(formatNumber(NaN)).toBe("—");
    expect(formatNumber(Infinity)).toBe("—");
    expect(formatNumber(-Infinity)).toBe("—");
  });
});

describe("confidenceLabel", () => {
  it.each<["medium" | "high" | "very_high", string]>([
    ["medium", "medium"],
    ["high", "high"],
    ["very_high", "very high"],
  ])("confidenceLabel(%s) === %j", (c, expected) => {
    expect(confidenceLabel(c)).toBe(expected);
  });
});
