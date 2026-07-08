// Type contracts that mirror backend/app/schemas/api.py.
// Copied from frontend/lib/api.ts during the Connect→Forge migration.
// The Connect-era apiClient/SSR helpers do not survive — Forge calls the
// backend via @forge/bridge requestRemote (see ./requestRemote.ts).

export type Confidence = "medium" | "high" | "very_high";

export interface Bottleneck {
  status: string;
  score: number;
  confidence: Confidence;
  reasons: string[];
  time_ratio: number | null;
  wip_ratio: number | null;
  throughput_delta: number | null;
  current_avg_seconds: number;
  previous_avg_seconds: number;
  current_wip: number;
  previous_wip: number;
  current_throughput: number;
  previous_throughput: number;
  wip_limit: number | null;
}

export interface Trend {
  metric: string;
  status: string | null;
  current_value: number;
  previous_value: number;
  ratio: number;
  change_pct: number;
  direction: "worsening" | "improving" | "stable";
}

export interface InsightResponse {
  window_start: string;
  window_end: string;
  previous_window_start: string;
  previous_window_end: string;
  bottleneck: Bottleneck | null;
  candidates: {
    status: string;
    score: number;
    time_ratio: number | null;
    wip_ratio: number | null;
    throughput_delta: number | null;
    reasons: string[];
  }[];
  trends: Trend[];
  explanation: string | null;
}

export interface StatusMetric {
  status: string;
  avg_seconds: number;
  p50_seconds: number;
  p90_seconds: number;
  wip_avg: number;
  throughput: number;
  sample_size: number;
  wip_limit: number | null;
}

export interface MetricsWindow {
  window_start: string;
  window_end: string;
  statuses: StatusMetric[];
  cycle_time_count: number;
  cycle_time_avg_seconds: number;
  cycle_time_p50_seconds: number;
  cycle_time_p90_seconds: number;
}

export interface MetricsResponse {
  current: MetricsWindow;
  previous: MetricsWindow;
}

export interface AlertOut {
  id: number;
  rule_id: string;
  rule_type: string;
  issue_id: string | null;
  status: string | null;
  triggered_at: string;
  payload: Record<string, unknown>;
}

// ----- Alert rules -----------------------------------------------

export type AlertRuleType =
  | "status_duration"
  | "cycle_time"
  | "no_activity"
  | "trend"
  | "wip_breach";

export interface AlertRule {
  id: string;
  type: AlertRuleType;
  enabled: boolean;
  config: Record<string, unknown>;
}

// ----- Alert delivery destinations (ADR-0037) -------------------------

export type AlertDestinationType = "email" | "slack" | "teams";

export interface AlertDestination {
  id: string;
  type: AlertDestinationType;
  name: string;
  // email: { address }; slack/teams: { webhook_url_set, webhook_url_hint } (masked)
  config: Record<string, unknown>;
  is_tenant_default: boolean;
  status: "active" | "disabled";
  last_test_at: string | null;
  last_test_status: string | null;
  recent_failure_count: number;
}

export interface AlertDestinationsResponse {
  destinations: AlertDestination[];
}

// Input shape for creating/updating a destination (config carries the raw
// address / webhook_url the customer enters).
export interface AlertDestinationIn {
  id: string;
  type: AlertDestinationType;
  name: string;
  config: Record<string, unknown>;
  is_tenant_default: boolean;
  status: "active" | "disabled";
}

export interface RuleDestinations {
  destination_ids: string[];
}

// ----- Tenant settings -----------------------------------------

export interface TenantSettings {
  active_statuses_override: string[] | null;
  effective_active_statuses: string[];
  done_statuses_override: string[] | null;
  effective_done_statuses: string[];
  terminal_statuses_override: string[] | null;
  effective_terminal_statuses: string[];
  // ADR-0042: per-tenant external-blocking status set. Excluded from
  // bottleneck attribution; still recorded in slice data + charts.
  external_blocking_statuses_override: string[] | null;
  effective_external_blocking_statuses: string[];
  // ADR-0038: when false (safe default), done_statuses auto-merge into
  // the effective terminal list. True keeps the lists fully independent
  // for advanced workflows. Surfaced in the Settings UI's Advanced section.
  independent_done_terminal_lists: boolean;

  bottleneck_time_ratio_threshold_override: number | null;
  effective_bottleneck_time_ratio_threshold: number;
  bottleneck_wip_ratio_threshold_override: number | null;
  effective_bottleneck_wip_ratio_threshold: number;
  bottleneck_throughput_delta_threshold_override: number | null;
  effective_bottleneck_throughput_delta_threshold: number;

  story_points_field_id: string | null;
  sprint_field_id: string | null;
}

export interface TenantSettingsIn {
  active_statuses?: string[] | null;
  done_statuses?: string[] | null;
  terminal_statuses?: string[] | null;
  // ADR-0042: null = drop override; empty array = explicit "no
  // external-blocking statuses" (distinct from null for this field only).
  external_blocking_statuses?: string[] | null;
  independent_done_terminal_lists?: boolean | null;
  bottleneck_time_ratio_threshold?: number | null;
  bottleneck_wip_ratio_threshold?: number | null;
  bottleneck_throughput_delta_threshold?: number | null;
  story_points_field_id?: string | null;
  sprint_field_id?: string | null;
}

export interface AlertsResponse {
  alerts: AlertOut[];
  total: number;
}

// ----- WIP Aging chart ------------------------------------------------

export interface WipAgingTicket {
  key: string;
  summary: string | null;
  status: string;
  days_in_status: number;
  cycle_days: number;
  assignee: string | null;
  priority: string | null;
  story_points: number | null;
  issue_type: string | null;
}

export interface WipAgingResponse {
  tickets: WipAgingTicket[];
  p95_cycle_days: number | null;
  sample_size: number;
}

// ----- CFD -----------------------------------------------------------

export interface CfdDay {
  date: string; // YYYY-MM-DD
  by_status: Record<string, number>;
}

export interface CfdResponse {
  window_start: string;
  window_end: string;
  statuses: string[];
  days: CfdDay[];
}

// ----- Cycle Time Scatter --------------------------------------------

export interface ScatterPoint {
  key: string;
  summary: string | null;
  completed_at: string;
  cycle_days: number;
  issue_type: string | null;
  priority: string | null;
  assignee: string | null;
}

export interface CycleScatterResponse {
  window_start: string;
  window_end: string;
  points: ScatterPoint[];
  p50_cycle_days: number | null;
  p85_cycle_days: number | null;
  p95_cycle_days: number | null;
}

// ----- WIP Limits ----------------------------------------------------

export interface WipLimit {
  project_key: string | null; // null = tenant-wide default
  status: string;
  max_in_progress: number;
  breach_minutes: number;
}

export interface WipLimitsResponse {
  limits: WipLimit[];
}

// ----- Sprints ------------------------------------------------------

export interface SprintOut {
  id: number;
  name: string;
  state: "active" | "closed" | "future";
  start_at: string | null;
  end_at: string | null;
  complete_at: string | null;
  board_id: number;
  project_key: string | null;
}

export interface SprintsResponse {
  sprints: SprintOut[];
}

// ----- Sync state + backfill ---------------------------------

export interface BackfillState {
  status: "pending" | "running" | "completed" | "failed" | null;
  totalIssues: number | null;
  processedIssues: number | null;
  startedAt: string | null;
  completedAt: string | null;
  // ADR-0033: timestamp the customer dismissed the dashboard completion
  // banner. Null → banner is shown on next dashboard load; non-null →
  // banner suppressed permanently for this backfill cycle.
  acknowledgedAt: string | null;
  error: string | null;
}

export interface SyncState {
  lastSyncedAt: string | null;
  backfill: BackfillState;
  // ADR-0033: destination for SES proactive-notification emails
  // (backfill completion, failure, 50k-cap-reached). Null means we
  // haven't captured one yet; Settings UI prompts the customer to add
  // one. Customer can also clear the field to opt out of notifications.
  adminContactEmail: string | null;
}


// ----- ADR-0043: Work Schedule -------------------------------------------

export interface WorkSchedule {
  id: number | null;
  name: string;
  timezone: string;
  working_days_mask: number;
  work_start_time: string;
  work_end_time: string;
  holidays: string[];
  enabled: boolean;
}

export type WorkScheduleIn = Omit<WorkSchedule, "id">;

export interface RecomputeStatus {
  status: "idle" | "pending" | "running" | "completed" | "failed";
  progress_pct: number;
  rows_processed: number;
  total_rows: number;
  started_at: string | null;
  error: string | null;
}
