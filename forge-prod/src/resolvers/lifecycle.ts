import api from "@forge/api";

import { enqueueBackfill } from "./backfill";

// Forge product event triggers (NOT @forge/resolver-wrapped). Triggers
// invoke the function directly with `{ payload, context }`; Resolver's
// `getDefinitions()` expects a `call.functionKey` wrapper that triggers
// don't supply, which surfaces as `Cannot read properties of undefined
// (reading 'functionKey')` at runtime. Plain async functions are the
// right shape for trigger handlers — see commit a494f8f for the
// precedent and ADR-0033 for the queue path that depends on this.

const BACKEND_URL = process.env.BACKEND_URL ?? "";

// `avi:forge:uninstalled:app` — forwards to the backend so the tenant
// row + cascaded data drops out. Forge auto-attaches a Forge Invocation
// Token to outbound api.fetch calls when the URL is allowlisted under
// permissions.external.fetch, which is how the backend authenticates.
export const lifecycleResolver = async () => {
  if (!BACKEND_URL) {
    console.error("BACKEND_URL not set; cannot forward uninstall event");
    return;
  }
  const res = await api.fetch(`${BACKEND_URL}/api/forge/lifecycle/uninstalled`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  if (!res.ok) {
    console.error(`Forge uninstall forward failed: ${res.status}`);
  }
};

// `avi:forge:installed:app` (ADR-0033): auto-enqueue a
// historical backfill the moment a customer installs. This restores
// the original ADR-0025 design — the browser-loop pivot in ADR-0032
// log-only'd this handler because the queue path was broken on the
// existing install at the time. With the queue rebuild per ADR-0033,
// auto-start on install is back to satisfying the locked outcome #1.
//
// Failure mode: if enqueueBackfill throws (e.g. Forge queue platform
// incident at install time), we log + swallow. The dashboard's Settings
// tab still surfaces the "Start historical backfill" button as a
// manual fallback so the customer can retry from there.
export const installLifecycleResolver = async () => {
  console.log("Forge install event received — enqueueing backfill (ADR-0033)");
  try {
    await enqueueBackfill();
  } catch (e) {
    console.error("Backfill enqueue on install failed:", e);
  }
};
