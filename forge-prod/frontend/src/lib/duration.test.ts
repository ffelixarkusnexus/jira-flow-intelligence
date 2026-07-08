// Locks cross-language parity between TS `humanDuration` and the backend
// Python `human_duration` (app/services/duration_format.py). Every input
// in this test corpus MUST produce the same output the Python test
// (`backend/tests/test_duration_format.py`) asserts on. If you change
// the rendering rules, change both sides together AND update both test
// corpora — drift between the two is a customer-visible bug.

import { describe, expect, it, vi } from "vitest";
import { humanDuration, relativeTime } from "./duration";

describe("humanDuration", () => {
  // The full corpus from backend/tests/test_duration_format.py — verbatim
  // to make parity drift detectable.
  it.each<[number | null, string]>([
    [null, ""],
    [0, "0 minutes"],
    [1, "0 minutes"],
    [59, "0 minutes"],
    [60, "1 minute"],
    [120, "2 minutes"],
    [1800, "30 minutes"],
    [3600, "1 hour"],
    [7200, "2 hours"],
    [3660, "1 hour"], // minutes suppressed when hours present
    [86400, "1 day"],
    [172800, "2 days"],
    [90000, "1 day, 1 hour"],
    // The exact numbers from the customer-reported bug on 2026-06-02:
    [604800, "7 days"],
    [625910, "7 days, 5 hours"],
    [259200, "3 days"],
    [345203, "3 days, 23 hours"],
    [2385949, "27 days, 14 hours"],
    // Float inputs (from time-delta calculations) coerce via Math.floor.
    [3600.5, "1 hour"],
    [60.9, "1 minute"],
  ])("humanDuration(%s) === %j", (seconds, expected) => {
    expect(humanDuration(seconds)).toBe(expected);
  });

  it("treats undefined the same as null", () => {
    expect(humanDuration(undefined)).toBe("");
  });

  it("suppresses minutes when days OR hours are present (1d 1h 1m → '1 day, 1 hour')", () => {
    // 86400 + 3600 + 60 = 90060 → "1 day, 1 hour" (minute is dropped)
    expect(humanDuration(90060)).toBe("1 day, 1 hour");
    // 1h 30m = 5400 → "1 hour" (minute is dropped because hour is present)
    expect(humanDuration(5400)).toBe("1 hour");
  });
});

describe("relativeTime", () => {
  // Inject `now` deterministically so the test isn't time-dependent.
  const NOW = new Date("2026-06-02T12:00:00Z").getTime();

  it.each<[string, string]>([
    // Same instant → 0m ago
    ["2026-06-02T12:00:00Z", "0m ago"],
    // 5 minutes ago
    ["2026-06-02T11:55:00Z", "5m ago"],
    // 59 minutes ago → still minutes
    ["2026-06-02T11:01:00Z", "59m ago"],
    // 60 minutes → flips to hours
    ["2026-06-02T11:00:00Z", "1h ago"],
    // 23 hours
    ["2026-06-01T13:00:00Z", "23h ago"],
    // 24 hours → flips to days
    ["2026-06-01T12:00:00Z", "1d ago"],
    // 3 days
    ["2026-05-30T12:00:00Z", "3d ago"],
  ])("relativeTime(%s) === %s", (iso, expected) => {
    expect(relativeTime(iso, NOW)).toBe(expected);
  });

  it("uses Date.now() when `now` is omitted (smoke test)", () => {
    const spy = vi.spyOn(Date, "now").mockReturnValue(NOW);
    try {
      expect(relativeTime("2026-06-02T11:55:00Z")).toBe("5m ago");
    } finally {
      spy.mockRestore();
    }
  });

  it("future timestamps clamp to '0m ago' rather than going negative", () => {
    expect(relativeTime("2026-06-02T13:00:00Z", NOW)).toBe("0m ago");
  });
});
