// Vitest test setup: extends RTL matchers + mocks the @forge/bridge
// runtime APIs so component tests can import components that call
// `invoke()` / `requestRemote()` without hitting a real Forge platform.
//
// The mock returns deterministic empty / no-op values; individual tests
// can override with `vi.spyOn(bridge, "invoke").mockResolvedValue(...)`
// when they need a specific response.

import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
});

// Default mocks for @forge/bridge — these cover the imports used across
// the Custom UI today (invoke, requestRemote, view, router). Components
// that need real responses can override per-test.
vi.mock("@forge/bridge", () => ({
  invoke: vi.fn().mockResolvedValue(undefined),
  requestRemote: vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    statusText: "OK",
    headers: {},
    body: "",
    json: async () => ({}),
    text: async () => "",
  }),
  view: {
    getContext: vi.fn().mockResolvedValue({}),
    submit: vi.fn(),
    close: vi.fn(),
    refresh: vi.fn(),
  },
  router: {
    open: vi.fn(),
    navigate: vi.fn(),
  },
}));
