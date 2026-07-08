from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, insert
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.tenant_context import TenantContext
from app.db.models import Issue, TimeSlice, Transition, WorkSchedule
from app.services.working_time import working_seconds_between


@dataclass(frozen=True)
class BuiltSlice:
    tenant_id: str
    issue_id: str
    status: str
    # ADR-0045: stable Jira status identifier carried forward from the
    # transition that started this slice. NULL when the upstream transition
    # had no id (legacy payloads, or the issue's initial-status slice with
    # no preceding transition row).
    status_id: str | None
    start_at: datetime
    end_at: datetime
    duration_seconds: int
    is_open: bool


def build_time_slices(
    issue: Issue,
    transitions: Sequence[Transition],
    *,
    now: datetime | None = None,
    done_statuses: Sequence[str] = (),
    work_schedule: WorkSchedule | None = None,
) -> list[BuiltSlice]:
    """Build time slices for an issue from its (sorted) transitions.

    Algorithm (per 10_CLAUDE_CODE_MASTER/05_data_processing_logic.md):

      prev_time = issue.created_at
      prev_status = transitions[0].from_status if transitions else issue.current_status
      for t in transitions:
          emit slice(prev_status, prev_time, t.transitioned_at)
          prev_time, prev_status = t.transitioned_at, t.to_status
      emit final slice(prev_status, prev_time, done_at if Done else NOW)

    Guarantees:
      - No gaps: each slice end == next slice start
      - No overlaps: slices are strictly sequential by construction
      - Idempotent: same input -> same output
      - Reopened issues continue accumulating (no reset)

    ADR-0045: each slice also carries `status_id`, propagated from the
    transition's `to_status_id` (or `from_status_id` for the pre-first-
    transition slice). NULL when the upstream transition lacks the id.
    """
    if now is None:
        now = utcnow()

    sorted_transitions = sorted(transitions, key=lambda t: (t.transitioned_at, t.id or 0))

    if sorted_transitions:
        prev_status = sorted_transitions[0].from_status or issue.current_status or "Unknown"
        prev_status_id = sorted_transitions[0].from_status_id
    else:
        prev_status = issue.current_status or "Unknown"
        prev_status_id = None

    prev_time = issue.created_at
    slices: list[BuiltSlice] = []

    for t in sorted_transitions:
        cur_time = t.transitioned_at
        if cur_time < prev_time:
            # Out-of-order changelog entry — skip rather than create negative duration.
            continue
        # ADR-0043: when a tenant has an active work schedule, durations
        # are working-time. work_schedule=None (or schedule.enabled=False)
        # falls back to calendar seconds — identical to pre-ADR-0043 math.
        duration = working_seconds_between(prev_time, cur_time, work_schedule)
        slices.append(
            BuiltSlice(
                tenant_id=issue.tenant_id,
                issue_id=issue.id,
                status=prev_status or "Unknown",
                status_id=prev_status_id,
                start_at=prev_time,
                end_at=cur_time,
                duration_seconds=duration,
                is_open=False,
            )
        )
        prev_time = cur_time
        prev_status = t.to_status or prev_status
        prev_status_id = t.to_status_id if t.to_status_id is not None else prev_status_id

    is_done = issue.done_at is not None or (
        issue.current_status in set(done_statuses) if done_statuses else False
    )
    if is_done and issue.done_at is not None:
        end_time = issue.done_at
        is_open = False
    else:
        end_time = now
        is_open = not is_done

    if end_time < prev_time:
        end_time = prev_time

    duration = working_seconds_between(prev_time, end_time, work_schedule)
    slices.append(
        BuiltSlice(
            tenant_id=issue.tenant_id,
            issue_id=issue.id,
            status=prev_status or "Unknown",
            status_id=prev_status_id,
            start_at=prev_time,
            end_at=end_time,
            duration_seconds=duration,
            is_open=is_open,
        )
    )
    return slices


def replace_time_slices(
    session: Session, tenant_id: str, issue_id: str, slices: Sequence[BuiltSlice]
) -> int:
    """Atomic replacement of slices for an issue (idempotent recompute)."""
    session.execute(
        delete(TimeSlice).where(TimeSlice.tenant_id == tenant_id, TimeSlice.issue_id == issue_id)
    )
    if not slices:
        return 0
    session.execute(
        insert(TimeSlice),
        [
            {
                "tenant_id": s.tenant_id,
                "issue_id": s.issue_id,
                "status": s.status,
                "status_id": s.status_id,
                "start_at": s.start_at,
                "end_at": s.end_at,
                "duration_seconds": s.duration_seconds,
                "is_open": s.is_open,
            }
            for s in slices
        ],
    )
    return len(slices)


def recompute_slices_for_issue(
    session: Session, issue: Issue, ctx: TenantContext, *, now: datetime | None = None
) -> int:
    transitions = list(issue.transitions)
    slices = build_time_slices(
        issue,
        transitions,
        now=now,
        done_statuses=ctx.done_statuses,
        # ADR-0043: thread the active schedule through so slice durations
        # are working-time when the tenant has one configured.
        work_schedule=ctx.work_schedule,
    )
    return replace_time_slices(session, issue.tenant_id, issue.id, slices)
