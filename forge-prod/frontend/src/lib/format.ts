export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "—";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

export function formatPercent(value: number, signed = false): string {
  if (!Number.isFinite(value)) return "—";
  const rounded = Math.round(value);
  if (signed) return `${rounded > 0 ? "+" : ""}${rounded}%`;
  return `${rounded}%`;
}

export function formatNumber(n: number, digits = 1): string {
  if (!Number.isFinite(n)) return "—";
  return n.toFixed(digits);
}

export function confidenceLabel(c: "medium" | "high" | "very_high"): string {
  return c.replace("_", " ");
}
