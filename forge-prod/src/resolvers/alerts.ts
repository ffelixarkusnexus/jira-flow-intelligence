// Alert evaluation scheduled-trigger resolvers (ADR-0037 phase 2).
//
// Two plain async exports (NOT Resolver-wrapped — scheduledTriggers invoke the
// function directly with no `call.functionKey`; see ADR-0033). Each is bound
// to its own scheduledTrigger in manifest.yml and
// calls the backend's tier-scoped evaluate-dispatch endpoint with that tier.
// invokeRemote attaches the per-install Forge Invocation Token, so the backend
// resolves the tenant and evaluates only that tenant's rules.
//
// Cadence tiering (ADR-0037 section A): day-scale-threshold rules run on the
// daily trigger; sub-24h-threshold rules on the hourly trigger. The backend
// short-circuits the hourly sweep for tenants with no sub-day rules.

import { invokeRemote } from "@forge/api";

const BACKEND_REMOTE_KEY = "backend";

async function evaluateDispatch(tier: "daily" | "hourly"): Promise<void> {
  const res = await invokeRemote(BACKEND_REMOTE_KEY, {
    path: `/api/alerts/evaluate-dispatch?tier=${tier}`,
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  if (!res.ok) {
    const text = await res.text();
    console.error(`Alert ${tier} eval-dispatch failed: ${res.status} ${text.slice(0, 200)}`);
    return;
  }
  const summary = (await res.json()) as { triggered?: number };
  console.log(`Alert ${tier} eval-dispatch: ${summary.triggered ?? 0} triggered`);
}

export const dailyAlertEvalResolver = async (): Promise<void> => {
  await evaluateDispatch("daily");
};

export const hourlyAlertEvalResolver = async (): Promise<void> => {
  await evaluateDispatch("hourly");
};
