// Vitest configuration for the Forge Custom UI test suite (ADR-0039).
//
// Inherits the Vite resolver / JSX transform from `vite.config.ts` —
// keeping one source of truth for "how TS/JSX gets transformed" is the
// reason we chose Vitest over Jest. See ADR-0039 §A for the rationale.
//
// Coverage shape:
//   - src/lib/**       → strict 80% gate (matches backend, locks
//                         cross-language parity tests like humanDuration)
//   - src/components/** → reported but not gated initially; tests grow
//                         organically with feature work
//
// Excludes the Vite build entry, test setup, and config files from
// the coverage denominator so the gate measures product code only.

import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    // Reset call history on every mock between tests so api-mock state
    // from one `vi.mocked(...).mockResolvedValue(...)` doesn't bleed into
    // the next test's "was this called once" assertion.
    clearMocks: true,
    coverage: {
      provider: "v8",
      reporter: ["text", "html", "lcov"],
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        "src/main.tsx",
        "src/test/**",
        "src/**/*.d.ts",
        "vitest.config.ts",
        "vite.config.ts",
      ],
      thresholds: {
        // Strict gate is scoped to the lib files this ADR (0039) actually
        // covers. requestRemote.ts (the @forge/bridge fetch wrapper, 204
        // lines, meaningful test work to mock the bridge surface) is an
        // explicit follow-up — surfaced in ADR-0039 §Implementation as
        // "deferred to a separate session." Listing files explicitly here
        // (rather than `src/lib/**`) prevents the gate from silently
        // passing/failing on whatever else lands in lib/ before its
        // tests catch up.
        "src/lib/duration.ts": {
          lines: 80,
          functions: 80,
          branches: 80,
          statements: 80,
        },
        "src/lib/alertGrouping.ts": {
          lines: 80,
          functions: 80,
          branches: 80,
          statements: 80,
        },
        "src/lib/format.ts": {
          lines: 80,
          functions: 80,
          branches: 80,
          statements: 80,
        },
      },
    },
  },
});
