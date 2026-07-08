from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# ----- Sync --------------------------------------------------------------------


class SyncRequest(BaseModel):
    jql: str | None = None


class SyncResult(BaseModel):
    issues_processed: int
    transitions_written: int
    slices_written: int
    errors: list[str] = Field(default_factory=list)


class IngestPayload(BaseModel):
    payloads: list[dict[str, Any]] = Field(
        ..., description="Raw Jira issue payloads with expanded changelog."
    )


# ----- Issues ------------------------------------------------------------------


class IssueOut(BaseModel):
    id: str
    key: str
    project_key: str | None
    summary: str | None
    issue_type: str | None
    current_status: str | None
    created_at: datetime
    updated_at: datetime
    done_at: datetime | None
    cycle_seconds: int | None
    active_seconds: int | None
    wait_seconds: int | None


class TimeSliceOut(BaseModel):
    status: str
    start_at: datetime
    end_at: datetime
    duration_seconds: int
    is_open: bool


class IssueDetailOut(IssueOut):
    time_slices: list[TimeSliceOut]


# ----- Metrics -----------------------------------------------------------------


class StatusMetricOut(BaseModel):
    status: str
    avg_seconds: float
    p50_seconds: float
    p90_seconds: float
    wip_avg: float
    throughput: int
    sample_size: int
    # When a wip_limit is configured for this (project, status), the
    # backend resolves it and includes it here so the UI can render the
    # actionable `current / limit` form. None = no limit configured.
    wip_limit: int | None = None


class MetricsWindowOut(BaseModel):
    window_start: datetime
    window_end: datetime
    statuses: list[StatusMetricOut]
    cycle_time_count: int
    cycle_time_avg_seconds: float
    cycle_time_p50_seconds: float
    cycle_time_p90_seconds: float


class MetricsResponse(BaseModel):
    current: MetricsWindowOut
    previous: MetricsWindowOut


# ----- Insights ----------------------------------------------------------------


class StatusSignalOut(BaseModel):
    status: str
    score: int
    time_ratio: float | None
    wip_ratio: float | None
    throughput_delta: float | None
    reasons: list[str]


class BottleneckOut(BaseModel):
    status: str
    score: int
    confidence: Literal["medium", "high", "very_high"]
    reasons: list[str]
    time_ratio: float | None
    wip_ratio: float | None
    throughput_delta: float | None
    current_avg_seconds: float
    previous_avg_seconds: float
    current_wip: float
    previous_wip: float
    current_throughput: int
    previous_throughput: int
    # Limit for the bottleneck stage if one is configured. None = unconfigured.
    wip_limit: int | None = None


class TrendOut(BaseModel):
    metric: str
    status: str | None
    current_value: float
    previous_value: float
    ratio: float
    change_pct: float
    direction: Literal["worsening", "improving", "stable"]


class InsightResponse(BaseModel):
    window_start: datetime
    window_end: datetime
    previous_window_start: datetime
    previous_window_end: datetime
    bottleneck: BottleneckOut | None
    candidates: list[StatusSignalOut]
    trends: list[TrendOut]
    explanation: str | None = None


# ----- WIP Aging chart -------------------------------------------------


class WipAgingTicket(BaseModel):
    """One in-flight ticket as it appears on the WIP Aging bubble chart."""

    key: str
    summary: str | None
    status: str
    days_in_status: float
    cycle_days: float
    assignee: str | None
    priority: str | None
    story_points: float | None
    issue_type: str | None


class WipAgingResponse(BaseModel):
    tickets: list[WipAgingTicket]
    # P95 of recent (last 90d) cycle times in days. The chart renders this as
    # an overlay line so users can see which in-flight tickets are aging
    # *past* normal cycle times — that's the "stuck" boundary.
    p95_cycle_days: float | None
    sample_size: int


# ----- Cumulative Flow Diagram ----------------------------------------


class CfdDay(BaseModel):
    date: str  # YYYY-MM-DD, end of day
    by_status: dict[str, int]


class CfdResponse(BaseModel):
    window_start: datetime
    window_end: datetime
    statuses: list[str]
    days: list[CfdDay]


# ----- Cycle Time Scatter ---------------------------------------------


class ScatterPointOut(BaseModel):
    key: str
    summary: str | None
    completed_at: str
    cycle_days: float
    issue_type: str | None
    priority: str | None
    assignee: str | None


class CycleScatterResponse(BaseModel):
    window_start: datetime
    window_end: datetime
    points: list[ScatterPointOut]
    p50_cycle_days: float | None
    p85_cycle_days: float | None
    p95_cycle_days: float | None


# ----- Alerts ------------------------------------------------------------------


class AlertOut(BaseModel):
    id: int
    rule_id: str
    rule_type: str
    issue_id: str | None
    status: str | None
    triggered_at: datetime
    payload: dict[str, Any]


class AlertRuleIn(BaseModel):
    id: str
    type: Literal["status_duration", "cycle_time", "no_activity", "trend", "wip_breach"]
    enabled: bool = True
    config: dict[str, Any]


class AlertRuleOut(AlertRuleIn):
    pass


class AlertsResponse(BaseModel):
    alerts: list[AlertOut]
    total: int


class EvaluateAlertsResponse(BaseModel):
    triggered: int
    alerts: list[AlertOut]


# ----- Alert delivery destinations (ADR-0037) -------------------------------


class AlertDestinationIn(BaseModel):
    id: str
    type: Literal["email", "slack", "teams"]
    name: str
    # email: {"address": ...}; slack/teams: {"webhook_url": ...}. Optional on
    # update: omit to preserve the stored config (e.g. toggling is_tenant_default
    # or re-enabling a paused destination without re-pasting the webhook URL).
    config: dict[str, Any] | None = None
    is_tenant_default: bool = False
    status: Literal["active", "disabled"] = "active"


class AlertDestinationOut(BaseModel):
    id: str
    type: str
    name: str
    config: dict[str, Any]  # webhook_url masked on output
    is_tenant_default: bool
    status: str
    last_test_at: datetime | None
    last_test_status: str | None
    recent_failure_count: int  # failed deliveries in the last 24h


class AlertDestinationsResponse(BaseModel):
    destinations: list[AlertDestinationOut]


class RuleDestinationsIn(BaseModel):
    destination_ids: list[str]
    override_cooldown_seconds: int | None = None


class RuleDestinationsOut(BaseModel):
    destination_ids: list[str]


# ----- WIP Limits -----------------------------------------------------------


class WipLimitIn(BaseModel):
    project_key: str | None = Field(
        default=None,
        description="Project scope for this limit. None = tenant-wide default.",
    )
    status: str
    max_in_progress: int = Field(ge=0)
    breach_minutes: int = Field(default=0, ge=0)


class WipLimitOut(WipLimitIn):
    pass


class WipLimitsResponse(BaseModel):
    limits: list[WipLimitOut]


# ----- Tenant settings -------------------------------------------------------


class TenantSettingsOut(BaseModel):
    """Per-tenant overrides + the effective values that combine override
    with default. Frontend reads `effective_*` to show what's actually in
    use; on save it sends the override fields (None means "use default")."""

    active_statuses_override: list[str] | None
    effective_active_statuses: list[str]
    done_statuses_override: list[str] | None
    effective_done_statuses: list[str]
    terminal_statuses_override: list[str] | None
    effective_terminal_statuses: list[str]
    # ADR-0042 — per-tenant external-blocking status set. Excluded from
    # bottleneck attribution; still recorded in slice data + charts. Opt-in,
    # ships empty.
    external_blocking_statuses_override: list[str] | None
    effective_external_blocking_statuses: list[str]
    # ADR-0038 — when False (safe default), done_statuses auto-merge into
    # the effective terminal list. True keeps the lists fully independent
    # for advanced workflows. Surfaced in the Settings UI's Advanced section.
    independent_done_terminal_lists: bool

    bottleneck_time_ratio_threshold_override: float | None
    effective_bottleneck_time_ratio_threshold: float
    bottleneck_wip_ratio_threshold_override: float | None
    effective_bottleneck_wip_ratio_threshold: float
    bottleneck_throughput_delta_threshold_override: float | None
    effective_bottleneck_throughput_delta_threshold: float

    story_points_field_id: str | None
    sprint_field_id: str | None


class TenantSettingsIn(BaseModel):
    """Setting any field to None drops the override (back to default)."""

    active_statuses: list[str] | None = None
    done_statuses: list[str] | None = None
    terminal_statuses: list[str] | None = None
    # ADR-0042: per-tenant external-blocking statuses. None drops override;
    # empty list is allowed and means "no statuses are external-blocking"
    # (distinct from active/done/terminal where empty would be footgun).
    external_blocking_statuses: list[str] | None = None
    independent_done_terminal_lists: bool | None = None
    bottleneck_time_ratio_threshold: float | None = None
    bottleneck_wip_ratio_threshold: float | None = None
    bottleneck_throughput_delta_threshold: float | None = None
    story_points_field_id: str | None = None
    sprint_field_id: str | None = None


# ----- ADR-0044: Issue View Panel ----------------------------------------


class IssuePanelSlice(BaseModel):
    status: str
    entered_at: datetime
    # None = current slice (issue still in this status).
    exited_at: datetime | None
    duration_seconds: int
    # ADR-0042 marker. The panel renders a visual indicator next to the
    # status so an engineer triaging the ticket sees at a glance that the
    # time isn't team-controllable.
    is_external_blocking: bool


class IssuePanelData(BaseModel):
    issue_key: str
    current_status: str
    status_history: list[IssuePanelSlice]
    total_cycle_time_seconds: int
    is_in_current_bottleneck: bool
    project_dashboard_url: str
