from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.db.types import UTCDateTime


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    """One Forge app installation on an Atlassian Cloud site.

    `client_key` is the opaque tenant identifier — historically the
    Connect clientKey, today the Forge installation ARI. Kept under that
    name (rather than renamed) because every tenanted FK across the
    schema still points at it; renaming would be a large migration with
    no functional payoff.
    """

    __tablename__ = "tenants"

    client_key: Mapped[str] = mapped_column(String, primary_key=True)
    cloud_id: Mapped[str | None] = mapped_column(String, index=True)
    base_url: Mapped[str] = mapped_column(String, nullable=False)
    display_url: Mapped[str | None] = mapped_column(String)
    product_type: Mapped[str] = mapped_column(String, default="jira", nullable=False)

    # Forge installation ARI. Unique when populated. The auth middleware
    # looks tenants up by this column on FIT validation.
    forge_installation_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)

    plan: Mapped[str] = mapped_column(String, default="free", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    installed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    # Per-tenant config that overrides Settings defaults. NULL = inherit default.
    active_statuses: Mapped[list[str] | None] = mapped_column(JSON)
    done_statuses: Mapped[list[str] | None] = mapped_column(JSON)
    terminal_statuses: Mapped[list[str] | None] = mapped_column(JSON)
    # ADR-0042: per-tenant external-blocking status set. Statuses in this
    # set are excluded from bottleneck attribution but still recorded in
    # time_slices (the team can see "yes, this ticket spent 8 days Blocked"
    # without that time driving the bottleneck card). NULL = inherit
    # Settings.external_blocking_statuses (ships as [] — opt-in feature).
    external_blocking_statuses: Mapped[list[str] | None] = mapped_column(JSON)
    # ADR-0038 (best-in-category-defaults hierarchy / CLAUDE.md rule #10).
    # NULL/False (the safe default) makes TenantContext.terminal_statuses
    # merge done_statuses INTO terminal regardless of any override — fixes
    # the "Done is the bottleneck" footgun where a tenant added a Done
    # status but forgot to add it to Terminal. True restores the prior
    # behavior (override is authoritative, no merge) for the rare advanced
    # workflow where Done is a transient state (Done → Verified → Released).
    independent_done_terminal_lists: Mapped[bool | None] = mapped_column(Boolean, default=False)

    # Backfill state. The Forge consumer paginates through all of the
    # tenant's Jira issues on first install (or on manual trigger from the
    # Settings tab for existing installs). Status drives the UI's progress
    # indicator; `next_page_token` lets the consumer resume after re-enqueue
    # when one batch chain exceeds the 10-min consumer budget.
    backfill_status: Mapped[str | None] = mapped_column(
        String
    )  # pending | running | completed | failed
    backfill_total_issues: Mapped[int | None] = mapped_column(Integer)
    backfill_processed_issues: Mapped[int | None] = mapped_column(Integer)
    backfill_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    backfill_completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    backfill_next_page_token: Mapped[str | None] = mapped_column(String)
    backfill_error: Mapped[str | None] = mapped_column(Text)
    bottleneck_time_ratio_threshold: Mapped[float | None] = mapped_column(Float)
    bottleneck_wip_ratio_threshold: Mapped[float | None] = mapped_column(Float)
    bottleneck_throughput_delta_threshold: Mapped[float | None] = mapped_column(Float)

    # Per-tenant custom-field overrides. NULL = use the static
    # fallback chain in ingestion_service. Set explicitly when a site's
    # Sprint or Story Points field has a non-standard ID — replaces the
    # heuristic detection. Story-points heuristic doesn't exist; Sprint
    # heuristic does, but the explicit override skips the probe.
    story_points_field_id: Mapped[str | None] = mapped_column(String)
    sprint_field_id: Mapped[str | None] = mapped_column(String)

    # ADR-0033 (consumer-queue backfill rebuild). Both columns are nullable
    # and populated lazily.
    # `backfill_acknowledged_at` — set when the customer dismisses the
    # dashboard completion banner. UI uses NULL → show banner; non-NULL → hide.
    # `admin_contact_email` — destination for SES completion / failure /
    # cap-reached emails per CLAUDE.md rule #9 (proactive-notification).
    # Populated either from the install lifecycle payload if Forge exposes
    # it, or via a Settings UI prompt; NULL means we haven't captured one
    # yet and the SES path logs a warning + skips the send.
    backfill_acknowledged_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    admin_contact_email: Mapped[str | None] = mapped_column(String)
    # `last_failure_digest_at` — when the 24h batched alert-delivery-failure
    # digest was last sent (ADR-0037). Guards the once-per-24h cadence.
    last_failure_digest_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    # ADR-0043 work schedule. NULL = calendar-time math (default, current
    # behavior). Non-NULL points at a WorkSchedule row whose `enabled` flag
    # is the authoritative on/off — keeping the schedule row around when
    # disabled means re-enabling restores the same configuration without
    # re-entry.
    active_work_schedule_id: Mapped[int | None] = mapped_column(
        ForeignKey("work_schedules.id", ondelete="SET NULL"), nullable=True
    )
    # `lazy="joined"` so a Tenant loaded via the request-scoped session has
    # the schedule eager-loaded in one query — TenantContext is used across
    # async-detached call paths (background services, after session close)
    # where a lazy load would trip DetachedInstanceError.
    active_work_schedule: Mapped["WorkSchedule | None"] = relationship(
        "WorkSchedule", foreign_keys=[active_work_schedule_id], lazy="joined"
    )
    # 2026-06-08 customer-facing URL fix follow-up. Forge's documented
    # deep-link URL form for jira:projectPage is `/jira/{projectType}/
    # projects/{key}/apps/{appId}/{envId}`. `envId` is Forge's per-
    # environment identifier (a string UUID), exposed on the
    # `@forge/bridge` FullContext as `environmentId`. It's NOT carried in
    # the FIT — the dashboard resolver pushes it to
    # `PUT /api/forge/sync/display-url` on every mount, alongside
    # `siteUrl`. Nullable because brand-new installs land here without
    # it until the first dashboard mount fires the resolver heartbeat;
    # URL helpers fall back to a transitional /boards URL during that
    # window.
    forge_env_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Recompute state machine: idle / pending / running / completed / failed.
    # Dashboard banner reads this and `recompute_progress_pct` to render the
    # progress indicator. `recompute_error` is the surfaced failure message
    # when the consumer trips.
    recompute_status: Mapped[str | None] = mapped_column(String, nullable=True)
    # Rows processed so far (NOT a percentage — at 500k-row tenants the
    # percentage would floor to 0 between batches). Convert to percentage
    # at API boundary: min(100, int(100 * rows_processed / total_rows)).
    recompute_rows_processed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recompute_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    recompute_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Issue(Base):
    """Composite PK `(tenant_id, id)` because Jira issue IDs are unique
    per-instance, not globally."""

    __tablename__ = "issues"
    __table_args__ = (Index("ix_issues_project", "tenant_id", "project_key"),)

    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.client_key", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)

    key: Mapped[str] = mapped_column(String, nullable=False, index=True)
    project_key: Mapped[str | None] = mapped_column(String)
    summary: Mapped[str | None] = mapped_column(Text)
    issue_type: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    done_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    current_status: Mapped[str | None] = mapped_column(String, index=True)
    # Assignee display name as Jira returned it; nullable when unassigned. Used
    # for color-grouping in the WIP Aging chart.
    assignee: Mapped[str | None] = mapped_column(String)
    # Atlassian accountId for the assignee — required for Marketplace
    # compliance with the Personal Data Reporting API
    # (https://developer.atlassian.com/platform/forge/user-privacy-guidelines/).
    # When Atlassian anonymizes a user account, we receive their accountId
    # via the periodic report-accounts polling and null-out both fields
    # for any matching issue rows. NOT used as a display field (we still
    # show `assignee` display name in charts) — purely the linkage key for
    # the anonymization protocol.
    assignee_account_id: Mapped[str | None] = mapped_column(String)
    # Priority name (e.g. "High", "Medium") — fallback bubble size when story
    # points are absent. Nullable since not every Jira instance uses priority.
    priority: Mapped[str | None] = mapped_column(String)
    # Story points (estimate). Nullable; comes from a per-tenant custom field
    # ID configured at install time — defaults to customfield_10016 (the most
    # common Jira Software default). Stored as float because some Jira
    # instances allow fractional points.
    story_points: Mapped[float | None] = mapped_column(Float)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    transitions: Mapped[list["Transition"]] = relationship(
        back_populates="issue", cascade="all, delete-orphan"
    )
    time_slices: Mapped[list["TimeSlice"]] = relationship(
        back_populates="issue", cascade="all, delete-orphan"
    )


class Transition(Base):
    __tablename__ = "transitions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "issue_id"],
            ["issues.tenant_id", "issues.id"],
            ondelete="CASCADE",
        ),
        # ADR-0045: dedupe stays keyed on (transitioned_at, to_status) — name,
        # not id. Reason: to_status_id is nullable for legacy rows + dual-
        # dialect UNIQUE-with-NULL semantics differ (Postgres treats NULLs as
        # distinct, older SQLite versions treated them as equal). to_status is
        # always populated, so dedupe protection on changelog replay stays
        # deterministic across both dialects.
        UniqueConstraint(
            "tenant_id", "issue_id", "transitioned_at", "to_status", name="uq_transition_dedupe"
        ),
        Index("ix_transitions_issue_ts", "tenant_id", "issue_id", "transitioned_at"),
        # ADR-0045: indexed lookups for ID-keyed aggregation. Composite with
        # tenant_id so multi-tenant queries hit the index.
        Index("ix_transitions_to_status_id", "tenant_id", "to_status_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False)
    issue_id: Mapped[str] = mapped_column(String, nullable=False)
    from_status: Mapped[str | None] = mapped_column(String)
    to_status: Mapped[str | None] = mapped_column(String)
    # ADR-0045: stable status identifiers from Jira's REST API. Nullable for
    # legacy rows (those written before the columns existed) and for callsites
    # that don't have the id available. Aggregate queries group by *_status_id
    # when populated and fall back to name-based grouping for NULL rows.
    from_status_id: Mapped[str | None] = mapped_column(String)
    to_status_id: Mapped[str | None] = mapped_column(String)
    transitioned_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)

    issue: Mapped["Issue"] = relationship(back_populates="transitions")


class TimeSlice(Base):
    __tablename__ = "time_slices"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "issue_id"],
            ["issues.tenant_id", "issues.id"],
            ondelete="CASCADE",
        ),
        Index("ix_slices_issue_start", "tenant_id", "issue_id", "start_at"),
        # Name-keyed index preserved for legacy NULL rows + render-time
        # display-name lookups during the mixed-mode aggregation window.
        Index("ix_slices_status_start", "tenant_id", "status", "start_at"),
        # ADR-0045: ID-keyed index for the post-fix aggregation path. Sits
        # alongside the name-keyed index — the planner uses whichever matches.
        Index("ix_slices_status_id_start", "tenant_id", "status_id", "start_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False)
    issue_id: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    # ADR-0045: stable status identifier propagated from
    # Transition.to_status_id. Nullable for legacy rows and for tenants whose
    # ingestion source doesn't provide status ids.
    status_id: Mapped[str | None] = mapped_column(String)
    start_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    end_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    is_open: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    issue: Mapped["Issue"] = relationship(back_populates="time_slices")


class IssueMetric(Base):
    __tablename__ = "metrics_issue"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "issue_id"],
            ["issues.tenant_id", "issues.id"],
            ondelete="CASCADE",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String, primary_key=True)
    issue_id: Mapped[str] = mapped_column(String, primary_key=True)
    cycle_seconds: Mapped[int | None] = mapped_column(Integer)
    active_seconds: Mapped[int | None] = mapped_column(Integer)
    wait_seconds: Mapped[int | None] = mapped_column(Integer)
    is_done: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)


class StatusWindowMetric(Base):
    __tablename__ = "metrics_status_window"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "status", "window_start", "window_end", name="uq_status_window"
        ),
        Index("ix_status_window", "tenant_id", "status", "window_start"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.client_key", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String, nullable=False)
    window_start: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    window_end: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    avg_seconds: Mapped[float | None] = mapped_column(Float)
    p50_seconds: Mapped[float | None] = mapped_column(Float)
    p90_seconds: Mapped[float | None] = mapped_column(Float)
    wip_avg: Mapped[float | None] = mapped_column(Float)
    throughput: Mapped[int | None] = mapped_column(Integer)
    sample_size: Mapped[int | None] = mapped_column(Integer)


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "rule_id", "issue_id", "status", "key", name="uq_alert_idempotency"
        ),
        Index("ix_alerts_triggered_at", "tenant_id", "triggered_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.client_key", ondelete="CASCADE"), nullable=False
    )
    rule_id: Mapped[str] = mapped_column(String, nullable=False)
    rule_type: Mapped[str] = mapped_column(String, nullable=False)
    issue_id: Mapped[str | None] = mapped_column(String)
    status: Mapped[str | None] = mapped_column(String)
    key: Mapped[str] = mapped_column(String, nullable=False)
    triggered_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class AlertRule(Base):
    __tablename__ = "alert_rules"

    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.client_key", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    type: Mapped[str] = mapped_column(String, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class AlertDeliveryDestination(Base):
    """A push destination for fired alerts — email, Slack, or Teams (ADR-0037).

    `config` holds channel-specific settings: `{"address": ...}` for email,
    `{"webhook_url": ...}` for Slack / Teams. `is_tenant_default` marks the
    destination as part of the tenant-wide default set that applies to any
    rule without an explicit `AlertRuleDestination` override.
    """

    __tablename__ = "alert_delivery_destinations"

    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.client_key", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    type: Mapped[str] = mapped_column(String, nullable=False)  # email | slack | teams
    name: Mapped[str] = mapped_column(String, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String, default="active", nullable=False)  # active|disabled
    is_tenant_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    last_test_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_test_status: Mapped[str | None] = mapped_column(String)


class AlertRuleDestination(Base):
    """Per-rule destination binding (ADR-0037). A rule's effective destination
    set is its explicit bindings here UNION the tenant-default destinations.
    `override_cooldown_seconds` NULL means use the rule/tenant default cooldown."""

    __tablename__ = "alert_rule_destinations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "alert_rule_id"],
            ["alert_rules.tenant_id", "alert_rules.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "destination_id"],
            ["alert_delivery_destinations.tenant_id", "alert_delivery_destinations.id"],
            ondelete="CASCADE",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String, primary_key=True)
    alert_rule_id: Mapped[str] = mapped_column(String, primary_key=True)
    destination_id: Mapped[str] = mapped_column(String, primary_key=True)
    override_cooldown_seconds: Mapped[int | None] = mapped_column(Integer)


class AlertFire(Base):
    """One row per dispatch attempt of an alert to a destination (ADR-0037).
    Drives anti-spam (cooldown checks read the latest `fired_at` for a
    `(rule, destination)`) and the 24h failure digest (rows with
    `status='failed'`)."""

    __tablename__ = "alert_fires"
    __table_args__ = (
        Index(
            "ix_alert_fires_lookup",
            "tenant_id",
            "alert_rule_id",
            "destination_id",
            "fired_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.client_key", ondelete="CASCADE"), nullable=False
    )
    alert_rule_id: Mapped[str] = mapped_column(String, nullable=False)
    destination_id: Mapped[str] = mapped_column(String, nullable=False)
    fired_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)  # delivered|failed|skipped_cooldown
    detail: Mapped[str | None] = mapped_column(Text)


class Sprint(Base):
    """A Jira Software sprint as fetched from `/rest/agile/1.0/board/{id}/sprint`.

    PK is composite `(tenant_id, id)` because sprint IDs are scoped per-Jira-
    instance, not globally. See ADR-0023 for the bucket semantics.
    """

    __tablename__ = "sprints"
    __table_args__ = (
        Index("ix_sprints_project", "tenant_id", "project_key"),
        Index("ix_sprints_state", "tenant_id", "state"),
    )

    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.client_key", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)  # active|closed|future
    start_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    end_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    complete_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    board_id: Mapped[int] = mapped_column(Integer, nullable=False)
    project_key: Mapped[str | None] = mapped_column(String)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class IssueSprint(Base):
    """Issue ↔ sprint membership. Issues can belong to multiple sprints over
    their lifetime (carry-over). Stored as a union; per-sprint metric
    attribution is computed in the analytics layer (ADR-0023)."""

    __tablename__ = "issue_sprints"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "issue_id"],
            ["issues.tenant_id", "issues.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "sprint_id"],
            ["sprints.tenant_id", "sprints.id"],
            ondelete="CASCADE",
        ),
        Index("ix_issue_sprints_sprint", "tenant_id", "sprint_id"),
    )

    tenant_id: Mapped[str] = mapped_column(String, primary_key=True)
    issue_id: Mapped[str] = mapped_column(String, primary_key=True)
    sprint_id: Mapped[int] = mapped_column(Integer, primary_key=True)


class WipLimit(Base):
    """Per-status WIP cap. Project-scoped row beats tenant-wide (NULL).

    Composite PK = (tenant_id, project_key, status) where project_key NULL
    is treated as "tenant-wide default for any project on this tenant."
    See ADR-0022 for the resolution semantics.
    """

    __tablename__ = "wip_limits"
    __table_args__ = (
        Index("ix_wip_limits_tenant", "tenant_id"),
        Index("ix_wip_limits_project", "tenant_id", "project_key"),
    )

    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.client_key", ondelete="CASCADE"), primary_key=True
    )
    # NULL = tenant-wide default; populated key = override for that project.
    # SQLite treats NULL as distinct from itself in unique indexes, so
    # composite PKs with a NULL component break — we use an empty string
    # sentinel ("") in DDL via a server_default to dodge that. Postgres
    # handles NULL-in-PK fine; the empty-string sentinel is harmless on PG
    # too. The application reads "" back as None via the property below.
    project_key: Mapped[str] = mapped_column(String, primary_key=True, default="")
    status: Mapped[str] = mapped_column(String, primary_key=True)
    max_in_progress: Mapped[int] = mapped_column(Integer, nullable=False)
    breach_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)

    @property
    def project_key_or_none(self) -> str | None:
        return self.project_key or None


class WorkSchedule(Base):
    """Per-tenant business-hours schedule (ADR-0043).

    NULL `tenants.active_work_schedule_id` (the safe default) makes all
    duration math fall back to calendar time — bit-for-bit identical to
    pre-ADR-0043 behavior. Activating a schedule (or editing/disabling one)
    enqueues a background recompute of every historical `time_slices` row
    via the recomputeTimeSlicesConsumer Forge consumer, so the tenant
    lands in a single consistent math model instead of a permanent blend.
    """

    __tablename__ = "work_schedules"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_work_schedules_tenant_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.client_key", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    timezone: Mapped[str] = mapped_column(String, nullable=False, default="UTC")
    # Bitmask: Mon=1, Tue=2, Wed=4, Thu=8, Fri=16, Sat=32, Sun=64. Mon-Fri = 31.
    working_days_mask: Mapped[int] = mapped_column(Integer, nullable=False)
    work_start_time: Mapped[str] = mapped_column(String, nullable=False, default="09:00:00")
    work_end_time: Mapped[str] = mapped_column(String, nullable=False, default="17:00:00")
    # JSON array of ISO date strings ("2026-12-25", "2027-01-01", ...).
    holidays: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


def init_db() -> None:
    from app.db.session import engine

    Base.metadata.create_all(engine)
