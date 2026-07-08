import React from "react";
import ReactDOM from "react-dom/client";
import { view } from "@forge/bridge";
import { App } from "./App";
import { IssuePanel } from "./IssuePanel";
import "./index.css";

// ADR-0044: the same static bundle (`main`) is mounted for both the project
// page and the issue panel modules. Forge sets a different module key on
// the context for each; we read it once and render the right surface.
//
// view.getContext() resolves asynchronously, so we render a tiny placeholder
// until we know which surface we're on. The placeholder is small enough that
// it doesn't flash visibly in practice.

async function bootstrap(): Promise<void> {
  let surface: "issue-panel" | "dashboard" = "dashboard";
  try {
    const ctx = (await view.getContext()) as { moduleKey?: string };
    const moduleKey = ctx?.moduleKey;
    if (typeof moduleKey === "string" && moduleKey.includes("issue-panel")) {
      surface = "issue-panel";
    }
  } catch {
    // If context fetch fails, default to the dashboard (project page) — that
    // remains the primary surface and is the long-standing entry point.
  }

  const root = ReactDOM.createRoot(document.getElementById("root")!);
  root.render(
    <React.StrictMode>{surface === "issue-panel" ? <IssuePanel /> : <App />}</React.StrictMode>,
  );
}

void bootstrap();
