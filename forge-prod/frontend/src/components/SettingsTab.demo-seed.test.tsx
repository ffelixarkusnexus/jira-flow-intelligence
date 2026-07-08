// Demo-seed exposure on the Settings tab.
//
// The DemoSeedPanel inside SettingsTab is the only surface that POSTs to
// /api/dev/seed-demo. This test pins the panel behavior — dev-only
// visibility, the seed call, the result/failure surfaces.
//
// Why test the panel directly instead of the whole SettingsTab tree:
// SettingsTab pulls together ~6 other panels (tenant settings, alert
// destinations, WIP limits, ...) each with their own api calls. Testing
// the demo-seed surface in isolation keeps the mock surface tight to the
// one method it exercises.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// SettingsTab transitively pulls in ~8 panels, each calling its own api
// methods on mount. We only care about `seedDemo` for these tests;
// every other method should return a never-resolving promise so the
// render doesn't crash but no other UI state ever advances.
//
// Use a Proxy as the mock object — any property access returns a vi.fn
// that yields a never-resolving promise — UNLESS the caller has already
// installed a specific mock for that method (e.g., seedDemo via the
// per-test mockResolvedValue). This avoids the "enumerate every api
// method SettingsTab uses" maintenance burden.
//
// vi.mock factories are hoisted, so the never-promise + the Proxy
// scaffolding both live inside the factory closure.
vi.mock("../lib/requestRemote", () => {
  const never = (): Promise<never> => new Promise(() => {});
  const seedDemo = vi.fn();
  const fallback = new Map<string, ReturnType<typeof vi.fn>>();
  const proxy = new Proxy(
    { seedDemo },
    {
      get(target, prop, receiver) {
        if (prop === "seedDemo") return target.seedDemo;
        if (typeof prop !== "string") {
          return Reflect.get(target, prop, receiver);
        }
        let fn = fallback.get(prop);
        if (!fn) {
          fn = vi.fn(never);
          fallback.set(prop, fn);
        }
        return fn;
      },
    },
  );
  return { api: proxy };
});

import { api } from "../lib/requestRemote";
import { SettingsTab } from "./SettingsTab";

// SettingsTab pulls in other panels that hit api.getX() methods we
// haven't mocked. Stub the rest so the render doesn't crash. We only
// care about the DemoSeedPanel for these tests.
vi.mock("./TenantSettingsPanel", () => ({
  TenantSettingsPanel: () => null,
}));
vi.mock("./AlertDestinationsPanel", () => ({
  AlertDestinationsPanel: () => null,
}));

const projectKey = "TESTPROJ";

beforeEach(() => {
  vi.mocked(api.seedDemo).mockReset();
});

describe("DemoSeedPanel — demo-seed exposure", () => {
  it("renders the seed button when environmentType is 'development'", () => {
    render(
      <SettingsTab
        projectKey={projectKey}
        knownStatuses={[]}
        environmentType="development"
      />,
    );
    expect(
      screen.getByRole("button", { name: /load demo data/i }),
    ).toBeInTheDocument();
  });

  it("does NOT render the demo-seed panel for production installs", () => {
    render(
      <SettingsTab
        projectKey={projectKey}
        knownStatuses={[]}
        environmentType="production"
      />,
    );
    expect(
      screen.queryByRole("button", { name: /load demo data/i }),
    ).not.toBeInTheDocument();
  });

  it("clicking the button seeds the calling tenant's project", async () => {
    vi.mocked(api.seedDemo).mockResolvedValue({
      issues: 250,
      transitions: 600,
      slices: 800,
    });
    const user = userEvent.setup();
    render(
      <SettingsTab
        projectKey={projectKey}
        knownStatuses={[]}
        environmentType="development"
      />,
    );
    await user.click(screen.getByRole("button", { name: /load demo data/i }));
    await waitFor(() => {
      expect(api.seedDemo).toHaveBeenCalledWith(projectKey);
    });
  });

  it("renders the seed result after a successful run", async () => {
    vi.mocked(api.seedDemo).mockResolvedValue({
      issues: 250,
      transitions: 600,
      slices: 800,
    });
    const user = userEvent.setup();
    render(
      <SettingsTab
        projectKey={projectKey}
        knownStatuses={[]}
        environmentType="development"
      />,
    );
    await user.click(screen.getByRole("button", { name: /load demo data/i }));
    expect(
      await screen.findByText(/Seeded 250 issues, 600 transitions, 800/),
    ).toBeInTheDocument();
  });

  it("disables the button while the seed is running", async () => {
    // Long-running promise so the in-flight state is observable.
    let resolveRun: (v: unknown) => void = () => {};
    vi.mocked(api.seedDemo).mockReturnValue(
      new Promise((r) => {
        resolveRun = r;
      }) as unknown as ReturnType<typeof api.seedDemo>,
    );
    const user = userEvent.setup();
    render(
      <SettingsTab
        projectKey={projectKey}
        knownStatuses={[]}
        environmentType="development"
      />,
    );
    await user.click(screen.getByRole("button", { name: /load demo data/i }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^seeding…/i })).toBeDisabled();
    });
    // Clean up the pending promise so the test doesn't leak.
    resolveRun({ issues: 0, transitions: 0, slices: 0 });
  });

  it("surfaces a failure message instead of the result block when the call rejects", async () => {
    vi.mocked(api.seedDemo).mockRejectedValue(
      new Error("backend returned 500"),
    );
    const user = userEvent.setup();
    render(
      <SettingsTab
        projectKey={projectKey}
        knownStatuses={[]}
        environmentType="development"
      />,
    );
    await user.click(screen.getByRole("button", { name: /load demo data/i }));
    expect(
      await screen.findByText(/Failed: backend returned 500/),
    ).toBeInTheDocument();
  });
});
