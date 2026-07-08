// ADR-0043 recompute consumer + producer.
//
// Same shape as the backfill consumer in ./backfill.ts:
//  - `enqueueRecompute()` is the producer — called from the dashboard's
//    `startRecompute` resolver invocation after the Settings UI saves a
//    new schedule.
//  - `recomputeConsumer` is the consumer — reads from the queue, calls the
//    backend's /api/forge/schedule/recompute-batch endpoint, and re-enqueues
//    itself if the response indicates more batches remain.
//
// Idempotency lives in the backend: working_seconds_between is pure, and
// the rows_processed cursor on tenants is monotonic. Safe to re-run on
// crash; safe to over-enqueue.

import { invokeRemote } from "@forge/api";
import { Queue } from "@forge/events";
import Resolver from "@forge/resolver";

const BACKEND_REMOTE_KEY = "backend";
const recomputeQueue = new Queue({ key: "recomputequeue" });

export async function enqueueRecompute(): Promise<void> {
  // The queue rejects empty-body payloads (observed in the backfill consumer
  // saga); the kick value is a tombstone marker so push() always succeeds.
  await recomputeQueue.push({ body: { kick: Date.now() } });
}

const startResolver = new Resolver();
startResolver.define("startRecompute", async () => {
  await enqueueRecompute();
  return { ok: true };
});

export const startRecompute = startResolver.getDefinitions();

// Consumer — invoked by Forge runtime when a task lands on `recomputequeue`.
// The function call doesn't receive an event body shape we care about; we
// just call /recompute-batch and re-enqueue if not done.
export async function recomputeConsumer(): Promise<void> {
  // Soft cap: a single consumer invocation processes up to N batches back-to-
  // back before re-enqueuing. Keeps wall-clock to the 15-min Forge timeout
  // without leaving stragglers if a chain is sparse.
  const MAX_BATCHES_PER_INVOCATION = 25;
  let done = false;
  let batches = 0;
  while (!done && batches < MAX_BATCHES_PER_INVOCATION) {
    const res = await invokeRemote(BACKEND_REMOTE_KEY, {
      method: "POST",
      path: "/api/forge/schedule/recompute-batch",
      headers: { "Content-Type": "application/json" },
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`recompute-batch failed: ${res.status} ${text.slice(0, 200)}`);
    }
    const body = (await res.json()) as {
      done: boolean;
      progress_pct: number;
      processed_in_batch: number;
    };
    done = body.done;
    batches += 1;
  }
  if (!done) {
    // Hit the per-invocation cap with more batches remaining — re-enqueue.
    await enqueueRecompute();
  }
}
