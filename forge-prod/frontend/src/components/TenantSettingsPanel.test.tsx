// Interaction tests for TenantSettingsPanel — focused on the ADR-0038
// surface (Advanced settings expander + independent_done_terminal_lists
// toggle) plus the load → save / load → reset round-trips. The status
// chip lists and threshold inputs are unchanged from prior work and
// out of scope here; this test file covers the ADR-0038 increment.

import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { TenantSettings } from "../lib/types";

// Mock the api module before importing the component (which imports api).
vi.mock("../lib/requestRemote", () => ({
  api: {
    getTenantSettings: vi.fn(),
    putTenantSettings: vi.fn(),
  },
}));

import { api } from "../lib/requestRemote";
import { TenantSettingsPanel } from "./TenantSettingsPanel";

const DEFAULT_SETTINGS: TenantSettings = {
  active_statuses_override: null,
  effective_active_statuses: ["In Progress", "Code Review"],
  done_statuses_override: null,
  effective_done_statuses: ["Done", "Closed", "Resolved"],
  terminal_statuses_override: null,
  effective_terminal_statuses: ["Done", "Closed", "Cancelled"],
  external_blocking_statuses_override: null,
  effective_external_blocking_statuses: [],
  independent_done_terminal_lists: false,
  bottleneck_time_ratio_threshold_override: null,
  effective_bottleneck_time_ratio_threshold: 1.3,
  bottleneck_wip_ratio_threshold_override: null,
  effective_bottleneck_wip_ratio_threshold: 1.2,
  bottleneck_throughput_delta_threshold_override: null,
  effective_bottleneck_throughput_delta_threshold: -0.2,
  story_points_field_id: null,
  sprint_field_id: null,
};

beforeEach(() => {
  vi.mocked(api.getTenantSettings).mockResolvedValue(DEFAULT_SETTINGS);
  vi.mocked(api.putTenantSettings).mockResolvedValue(DEFAULT_SETTINGS);
});

describe("TenantSettingsPanel — loading state", () => {
  it("shows the loading placeholder before settings arrive", () => {
    vi.mocked(api.getTenantSettings).mockReturnValue(new Promise(() => {})); // never resolves
    render(<TenantSettingsPanel knownStatuses={[]} />);
    expect(screen.getByText(/loading tenant settings/i)).toBeInTheDocument();
  });

  it("renders the panel after settings load", async () => {
    render(<TenantSettingsPanel knownStatuses={[]} />);
    expect(
      await screen.findByRole("heading", { name: /tenant configuration/i }),
    ).toBeInTheDocument();
  });
});

describe("TenantSettingsPanel — Advanced settings expander (ADR-0038)", () => {
  it("renders the Advanced settings expander", async () => {
    render(<TenantSettingsPanel knownStatuses={[]} />);
    expect(await screen.findByText("Advanced settings")).toBeInTheDocument();
  });

  it("the Advanced expander is collapsed by default (cognitive load goes to power users only)", async () => {
    render(<TenantSettingsPanel knownStatuses={[]} />);
    await screen.findByText("Advanced settings");
    // The independent-lists toggle is rendered inside the <details>
    // element but the content is not visible until it's opened. jsdom
    // exposes the element regardless of open state, so test via the
    // `details.open` attribute instead.
    const summaries = document.querySelectorAll("details");
    expect(summaries.length).toBeGreaterThan(0);
    const advancedDetails = Array.from(summaries).find((d) =>
      d.textContent?.includes("Advanced settings"),
    );
    expect(advancedDetails).toBeDefined();
    expect(advancedDetails?.hasAttribute("open")).toBe(false);
  });

  it("the independent_done_terminal_lists toggle defaults to unchecked", async () => {
    render(<TenantSettingsPanel knownStatuses={[]} />);
    await screen.findByText("Advanced settings");
    const toggle = screen.getByRole("checkbox", {
      name: /manage done and terminal as fully independent lists/i,
    });
    expect(toggle).not.toBeChecked();
  });

  it("toggling the checkbox flips independent_done_terminal_lists in the save payload", async () => {
    const user = userEvent.setup();
    render(<TenantSettingsPanel knownStatuses={[]} />);
    await screen.findByText("Advanced settings");

    const toggle = screen.getByRole("checkbox", {
      name: /manage done and terminal as fully independent lists/i,
    });
    await user.click(toggle);
    expect(toggle).toBeChecked();

    await user.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(api.putTenantSettings).toHaveBeenCalledTimes(1);
    });
    const payload = vi.mocked(api.putTenantSettings).mock.calls[0][0];
    expect(payload.independent_done_terminal_lists).toBe(true);
  });

  it("toggle reflects the loaded setting when it's already true on the server", async () => {
    vi.mocked(api.getTenantSettings).mockResolvedValue({
      ...DEFAULT_SETTINGS,
      independent_done_terminal_lists: true,
    });
    render(<TenantSettingsPanel knownStatuses={[]} />);
    await screen.findByText("Advanced settings");
    const toggle = screen.getByRole("checkbox", {
      name: /manage done and terminal as fully independent lists/i,
    });
    expect(toggle).toBeChecked();
  });
});

describe("TenantSettingsPanel — save / reset", () => {
  it("Save sends the whole payload via api.putTenantSettings", async () => {
    const user = userEvent.setup();
    render(<TenantSettingsPanel knownStatuses={[]} />);
    await screen.findByText("Advanced settings");

    await user.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(api.putTenantSettings).toHaveBeenCalledTimes(1);
    });
    const payload = vi.mocked(api.putTenantSettings).mock.calls[0][0];
    // The payload mirrors the loaded settings (no edits made in this test).
    expect(payload.independent_done_terminal_lists).toBe(false);
    expect(payload.active_statuses).toBeNull();
  });

  it("Reset to defaults sends an empty payload object", async () => {
    const user = userEvent.setup();
    render(<TenantSettingsPanel knownStatuses={[]} />);
    await screen.findByText("Advanced settings");

    await user.click(
      screen.getByRole("button", { name: /reset all to defaults/i }),
    );

    await waitFor(() => {
      expect(api.putTenantSettings).toHaveBeenCalledTimes(1);
    });
    const payload = vi.mocked(api.putTenantSettings).mock.calls[0][0];
    expect(payload).toEqual({});
  });

  it("surfaces an error message when the API call rejects", async () => {
    vi.mocked(api.putTenantSettings).mockRejectedValueOnce(
      new Error("403 Forbidden"),
    );
    const user = userEvent.setup();
    render(<TenantSettingsPanel knownStatuses={[]} />);
    await screen.findByText("Advanced settings");

    await user.click(screen.getByRole("button", { name: /^save$/i }));

    expect(await screen.findByText(/403 forbidden/i)).toBeInTheDocument();
  });
});
