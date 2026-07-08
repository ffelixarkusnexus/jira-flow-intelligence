// Cross-language mirror of backend `app.services.duration_format.human_duration`.
// Must produce byte-identical output for any given numeric input so the
// in-product alerts list and the outbound Slack/Teams/email messages
// (which are formatted on the backend) read consistently. Drift between
// the two implementations is a customer-visible bug class — see ADR-0039
// for why this lives in a shared module + has a parity-locking test.
//
// Collapse rules (verbatim from the backend):
//   - days + hours when both nonzero
//   - days alone when days > 0 and hours == 0
//   - hours alone when hours > 0 and days == 0
//   - minutes only when days == 0 and hours == 0 (sub-hour case)
// `null` / `undefined` → "" so callers can detect "no value" and omit the
// slot rather than rendering a placeholder.

export function humanDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "";
  const s = Math.floor(seconds);
  const days = Math.floor(s / 86400);
  const remAfterDays = s - days * 86400;
  const hours = Math.floor(remAfterDays / 3600);
  const minutes = Math.floor((remAfterDays - hours * 3600) / 60);
  const parts: string[] = [];
  if (days) parts.push(`${days} day${days !== 1 ? "s" : ""}`);
  if (hours) parts.push(`${hours} hour${hours !== 1 ? "s" : ""}`);
  if (!days && !hours) {
    parts.push(`${minutes} minute${minutes !== 1 ? "s" : ""}`);
  }
  return parts.join(", ");
}

// Best-effort relative time for the alerts list timestamp suffix
// ("15h ago" / "3d ago"). Backend doesn't compute this — pure client
// rendering against `Date.now()`. Kept here in `lib/` so component
// tests can mock `Date.now()` deterministically.

export function relativeTime(iso: string, now: number = Date.now()): string {
  const t = new Date(iso).getTime();
  const diff = Math.max(0, now - t);
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}
