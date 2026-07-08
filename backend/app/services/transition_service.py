from dataclasses import dataclass
from datetime import datetime
from typing import Any

from dateutil import parser as dtparser
from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.db.models import Transition


@dataclass(frozen=True)
class ParsedTransition:
    tenant_id: str
    issue_id: str
    from_status: str | None
    to_status: str | None
    # ADR-0045: stable Jira status identifiers, both source and target. NULL
    # for payloads predating the column (legacy data) or webhooks that omit
    # the field. Aggregate queries group by *_status_id when populated and
    # fall back to name when NULL.
    from_status_id: str | None
    to_status_id: str | None
    transitioned_at: datetime


def _parse_ts(raw: str) -> datetime:
    return dtparser.isoparse(raw)


def extract_transitions_from_payload(
    tenant_id: str, issue_id: str, raw_payload: dict[str, Any]
) -> list[ParsedTransition]:
    """Extract status transitions from a Jira issue payload (with changelog).

    Source of truth = changelog. We keep ONLY status field changes,
    deduplicate (tenant_id, issue_id, transitioned_at, to_status), and sort ASC.

    Per Atlassian's changelog item shape: `fromString`/`toString` are status
    names, `from`/`to` are stable status IDs (ADR-0045). When `to`/`from` are
    present, they survive renames where `fromString`/`toString` change.
    """
    histories = (
        raw_payload.get("changelog", {}).get("histories")
        or raw_payload.get("changelog", {}).get("values")
        or []
    )

    seen: set[tuple[str, str, datetime, str | None]] = set()
    transitions: list[ParsedTransition] = []

    for history in histories:
        ts_raw = history.get("created")
        if not ts_raw:
            continue
        ts = _parse_ts(ts_raw)

        for item in history.get("items", []):
            if item.get("field") != "status":
                continue
            from_status = item.get("fromString")
            to_status = item.get("toString")
            # `from` / `to` are the stable status IDs in the Jira changelog
            # payload. Coerced to str because Jira sometimes returns int.
            from_raw = item.get("from")
            to_raw = item.get("to")
            from_status_id = str(from_raw) if from_raw is not None else None
            to_status_id = str(to_raw) if to_raw is not None else None
            key = (tenant_id, issue_id, ts, to_status)
            if key in seen:
                continue
            seen.add(key)
            transitions.append(
                ParsedTransition(
                    tenant_id=tenant_id,
                    issue_id=issue_id,
                    from_status=from_status,
                    to_status=to_status,
                    from_status_id=from_status_id,
                    to_status_id=to_status_id,
                    transitioned_at=ts,
                )
            )

    transitions.sort(key=lambda t: t.transitioned_at)
    return transitions


def replace_transitions(
    session: Session, tenant_id: str, issue_id: str, transitions: list[ParsedTransition]
) -> int:
    """Idempotent replacement of all transitions for an issue.

    Idempotency strategy: delete-and-insert per (tenant_id, issue_id). The unique
    constraint defends against duplicates if the delete is skipped, but full
    replacement guarantees the latest extraction is the single source of truth.
    """
    session.execute(
        delete(Transition).where(Transition.tenant_id == tenant_id, Transition.issue_id == issue_id)
    )
    if not transitions:
        return 0

    rows = [
        {
            "tenant_id": t.tenant_id,
            "issue_id": t.issue_id,
            "from_status": t.from_status,
            "to_status": t.to_status,
            "from_status_id": t.from_status_id,
            "to_status_id": t.to_status_id,
            "transitioned_at": t.transitioned_at,
        }
        for t in transitions
    ]
    session.execute(insert(Transition), rows)
    return len(rows)


def load_transitions(session: Session, tenant_id: str, issue_id: str) -> list[Transition]:
    return list(
        session.scalars(
            select(Transition)
            .where(Transition.tenant_id == tenant_id, Transition.issue_id == issue_id)
            .order_by(Transition.transitioned_at.asc(), Transition.id.asc())
        )
    )
