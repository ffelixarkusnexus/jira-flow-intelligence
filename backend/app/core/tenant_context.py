"""TenantContext — bundles a `Tenant` row with process-wide defaults so services
read configuration from a single object. Per-tenant overrides win; otherwise
the value comes from `Settings`.

This is the seam through which the multi-tenant refactor lands: every service
function takes a `TenantContext` instead of a `Settings`, and can read tenant
identity (`ctx.tenant_id`) and tenant-scoped config (`ctx.active_statuses`)
from the same object.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings
from app.db.models import Tenant


@dataclass(frozen=True)
class TenantContext:
    tenant: Tenant
    settings: Settings

    @property
    def tenant_id(self) -> str:
        return self.tenant.client_key

    @property
    def active_statuses(self) -> list[str]:
        return self.tenant.active_statuses or self.settings.active_statuses

    @property
    def done_statuses(self) -> list[str]:
        return self.tenant.done_statuses or self.settings.done_statuses

    @property
    def terminal_statuses(self) -> list[str]:
        """Workflow endpoints — done plus rejected/cancelled/etc. The CFD,
        bottleneck detection, and trend computation all exclude these.

        Per ADR-0038 / CLAUDE.md rule #10 (best-in-category-defaults
        hierarchy): the safe default merges `done_statuses` INTO terminal
        ALWAYS — regardless of whether the tenant overrode `terminal_statuses`.
        This fixes the footgun where a tenant added a Done status (e.g.
        "DEPLOYED" or "DONE" all-caps) but forgot to mirror it into terminal,
        producing nonsensical "Done is the current bottleneck" insights.

        Tenants with the rare workflow where Done is transient
        (Done → Verified → Released) can flip `independent_done_terminal_lists`
        to True in Tenant Configuration, which restores the prior behavior
        (override authoritative, no merge). Case-folded matching happens at
        the call site.
        """
        base_terminal = (
            self.tenant.terminal_statuses
            if self.tenant.terminal_statuses is not None
            else self.settings.terminal_statuses
        )
        if self.tenant.independent_done_terminal_lists:
            return list(base_terminal)
        merged = {*base_terminal, *self.done_statuses}
        return sorted(merged)

    @property
    def work_schedule(self):  # type: ignore[no-untyped-def]
        """ADR-0043 active work schedule (or None for calendar-time math).
        Returns the WorkSchedule ORM row via the lazy relationship; the
        `working_time.working_seconds_between` helper handles None by
        falling back to calendar seconds, which is the default behavior
        for every existing install."""
        return self.tenant.active_work_schedule

    @property
    def external_blocking_statuses(self) -> list[str]:
        """Statuses where work is paused waiting on a third party (customer,
        vendor, external review). Per ADR-0042 these are excluded from the
        bottleneck card's attribution step, but slice durations and charts
        still record and display them — the team sees "yes, this ticket spent
        8 days Blocked" without that time driving "which status is the
        bottleneck?". Default empty (opt-in feature; current behavior preserved
        when nothing is configured)."""
        if self.tenant.external_blocking_statuses is not None:
            return self.tenant.external_blocking_statuses
        return self.settings.external_blocking_statuses

    @property
    def bottleneck_time_ratio_threshold(self) -> float:
        return (
            self.tenant.bottleneck_time_ratio_threshold
            if self.tenant.bottleneck_time_ratio_threshold is not None
            else self.settings.bottleneck_time_ratio_threshold
        )

    @property
    def bottleneck_time_ratio_extra_threshold(self) -> float:
        return self.settings.bottleneck_time_ratio_extra_threshold

    @property
    def bottleneck_wip_ratio_threshold(self) -> float:
        return (
            self.tenant.bottleneck_wip_ratio_threshold
            if self.tenant.bottleneck_wip_ratio_threshold is not None
            else self.settings.bottleneck_wip_ratio_threshold
        )

    @property
    def bottleneck_throughput_delta_threshold(self) -> float:
        return (
            self.tenant.bottleneck_throughput_delta_threshold
            if self.tenant.bottleneck_throughput_delta_threshold is not None
            else self.settings.bottleneck_throughput_delta_threshold
        )

    @property
    def bottleneck_min_score(self) -> int:
        return self.settings.bottleneck_min_score

    @property
    def trend_increase_threshold(self) -> float:
        return self.settings.trend_increase_threshold

    @property
    def trend_decrease_threshold(self) -> float:
        return self.settings.trend_decrease_threshold

    @property
    def story_points_field_id(self) -> str | None:
        """Per-tenant override for the Story Points custom field. None
        falls back to the static candidate chain in ingestion_service."""
        return self.tenant.story_points_field_id

    @property
    def sprint_field_id(self) -> str | None:
        """Per-tenant override for the Sprint custom field. None falls
        back to the static candidate chain + heuristic shape probe."""
        return self.tenant.sprint_field_id
