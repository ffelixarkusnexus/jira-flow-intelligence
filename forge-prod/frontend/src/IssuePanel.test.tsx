// ADR-0044: Issue View Panel test.
//
// The panel reads via the Forge bridge `invoke("getIssueData")` which
// proxies to the backend's /api/forge/issue/{key}/panel-data endpoint.
// Mock @forge/bridge here so the panel can be rendered standalone.

import { describe, expect, it, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

const invokeMock = vi.fn();

vi.mock("@forge/bridge", () => ({
  invoke: (...args: unknown[]) => invokeMock(...args),
}));

import { IssuePanel } from "./IssuePanel";

const PANEL_DATA = {
  issue_key: "ABC-123",
  current_status: "Review",
  status_history: [
    {
      status: "In Progress",
      entered_at: "2026-06-01T08:00:00Z",
      exited_at: "2026-06-02T12:00:00Z",
      duration_seconds: 100_800,
      is_external_blocking: false,
    },
    {
      status: "Blocked",
      entered_at: "2026-06-02T12:00:00Z",
      exited_at: "2026-06-04T12:00:00Z",
      duration_seconds: 172_800,
      is_external_blocking: true,
    },
    {
      status: "Review",
      entered_at: "2026-06-04T12:00:00Z",
      exited_at: null,
      duration_seconds: 86_400,
      is_external_blocking: false,
    },
  ],
  total_cycle_time_seconds: 360_000,
  is_in_current_bottleneck: false,
  project_dashboard_url: "/jira/software/projects/ABC/boards",
};

beforeEach(() => {
  invokeMock.mockReset();
});

describe("IssuePanel", () => {
  it("renders status history with external-blocking marker", async () => {
    invokeMock.mockResolvedValue(PANEL_DATA);
    render(<IssuePanel />);
    await waitFor(() => screen.getByText("In Progress"));
    expect(screen.getByText("Blocked")).toBeInTheDocument();
    expect(screen.getByText("Review")).toBeInTheDocument();
    // External-blocking marker shows next to Blocked.
    expect(screen.getByText("external-blocking")).toBeInTheDocument();
    // "current" marker on the active slice.
    expect(screen.getByText("current")).toBeInTheDocument();
    // Deep link present.
    const link = screen.getByText("Open in Jira Flow Intelligence →");
    expect(link.getAttribute("href")).toBe("/jira/software/projects/ABC/boards");
  });

  it("hides the bottleneck-contribution badge when not in the bottleneck", async () => {
    invokeMock.mockResolvedValue(PANEL_DATA);
    render(<IssuePanel />);
    await waitFor(() => screen.getByText("In Progress"));
    expect(
      screen.queryByText(/currently named bottleneck/i),
    ).not.toBeInTheDocument();
  });

  it("shows the bottleneck-contribution badge when the issue is in the bottleneck", async () => {
    invokeMock.mockResolvedValue({ ...PANEL_DATA, is_in_current_bottleneck: true });
    render(<IssuePanel />);
    await waitFor(() => screen.getByText(/currently named bottleneck/i));
  });

  it("renders an error message when the bridge returns an error envelope", async () => {
    invokeMock.mockResolvedValue({ error: "backend-error", message: "boom" });
    render(<IssuePanel />);
    await waitFor(() => screen.getByRole("alert"));
    expect(screen.getByRole("alert")).toHaveTextContent(/boom/);
  });
});
