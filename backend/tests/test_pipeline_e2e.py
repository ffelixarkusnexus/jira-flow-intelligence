from app.core.tenant_context import TenantContext
from app.services.ingestion_service import process_payloads


def _payload(
    key: str,
    created: str,
    transitions: list[tuple[str, str, str]],
    current: str,
    resolution: str | None = None,
) -> dict:
    return {
        "id": key,
        "key": key,
        "fields": {
            "created": created,
            "updated": created,
            "status": {"name": current},
            "issuetype": {"name": "Story"},
            "project": {"key": "ABC"},
            "summary": f"{key} test",
            "resolutiondate": resolution,
        },
        "changelog": {
            "histories": [
                {
                    "created": ts,
                    "items": [
                        {
                            "field": "status",
                            "fromString": frm,
                            "toString": to,
                        }
                    ],
                }
                for ts, frm, to in transitions
            ]
        },
    }


def test_ingestion_e2e_writes_issue_transitions_and_slices(session, ctx: TenantContext):
    payload = _payload(
        "ABC-1",
        "2026-01-01T10:00:00Z",
        [
            ("2026-01-01T12:00:00Z", "In Progress", "Review"),
            ("2026-01-01T14:00:00Z", "Review", "Done"),
        ],
        current="Done",
        resolution="2026-01-01T14:00:00Z",
    )
    report = process_payloads(session, [payload], ctx)
    session.commit()

    assert report.issues_processed == 1
    assert report.transitions_written == 2
    assert report.slices_written == 3  # In Progress, Review, Done(zero-length)

    from sqlalchemy import select

    from app.db.models import Issue, TimeSlice

    issue = session.get(Issue, (ctx.tenant_id, "ABC-1"))
    assert issue is not None
    assert issue.done_at is not None

    slices = list(
        session.scalars(
            select(TimeSlice).where(
                TimeSlice.tenant_id == ctx.tenant_id, TimeSlice.issue_id == "ABC-1"
            )
        )
    )
    by_status = {s.status: s.duration_seconds for s in slices}
    assert by_status["In Progress"] == 7200
    assert by_status["Review"] == 7200


def test_ingestion_idempotent_running_twice_produces_same_state(session, ctx: TenantContext):
    payload = _payload(
        "ABC-2",
        "2026-01-01T10:00:00Z",
        [("2026-01-01T14:00:00Z", "In Progress", "Done")],
        current="Done",
        resolution="2026-01-01T14:00:00Z",
    )
    process_payloads(session, [payload], ctx)
    session.commit()

    from sqlalchemy import select

    from app.db.models import TimeSlice, Transition

    transitions_a = list(session.scalars(select(Transition)))
    slices_a = list(session.scalars(select(TimeSlice)))

    process_payloads(session, [payload], ctx)
    session.commit()
    transitions_b = list(session.scalars(select(Transition)))
    slices_b = list(session.scalars(select(TimeSlice)))

    assert len(transitions_a) == len(transitions_b)
    assert len(slices_a) == len(slices_b)
