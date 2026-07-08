from app.services.transition_service import extract_transitions_from_payload


def test_extract_basic_canonical_payload():
    payload = {
        "id": "10001",
        "key": "ABC-123",
        "fields": {
            "created": "2026-01-01T10:00:00.000Z",
            "status": {"name": "Done"},
        },
        "changelog": {
            "histories": [
                {
                    "created": "2026-01-01T12:00:00.000Z",
                    "items": [
                        {
                            "field": "status",
                            "fromString": "In Progress",
                            "toString": "Review",
                        }
                    ],
                },
                {
                    "created": "2026-01-01T14:00:00.000Z",
                    "items": [
                        {
                            "field": "status",
                            "fromString": "Review",
                            "toString": "Done",
                        }
                    ],
                },
            ]
        },
    }
    result = extract_transitions_from_payload("t1", "10001", payload)
    assert len(result) == 2
    assert all(t.tenant_id == "t1" for t in result)
    assert result[0].from_status == "In Progress"
    assert result[0].to_status == "Review"
    assert result[1].from_status == "Review"
    assert result[1].to_status == "Done"


def test_filters_non_status_changes():
    payload = {
        "changelog": {
            "histories": [
                {
                    "created": "2026-01-01T12:00:00Z",
                    "items": [
                        {"field": "summary", "fromString": "a", "toString": "b"},
                        {
                            "field": "status",
                            "fromString": "Todo",
                            "toString": "Doing",
                        },
                    ],
                }
            ]
        }
    }
    result = extract_transitions_from_payload("t1", "X", payload)
    assert len(result) == 1
    assert result[0].to_status == "Doing"


def test_dedupes_identical_transitions():
    history = {
        "created": "2026-01-01T12:00:00Z",
        "items": [
            {"field": "status", "fromString": "A", "toString": "B"},
            {"field": "status", "fromString": "A", "toString": "B"},
        ],
    }
    payload = {"changelog": {"histories": [history]}}
    result = extract_transitions_from_payload("t1", "X", payload)
    assert len(result) == 1


def test_sorts_strictly_ascending_when_changelog_unsorted():
    payload = {
        "changelog": {
            "histories": [
                {
                    "created": "2026-01-01T14:00:00Z",
                    "items": [{"field": "status", "fromString": "B", "toString": "C"}],
                },
                {
                    "created": "2026-01-01T12:00:00Z",
                    "items": [{"field": "status", "fromString": "A", "toString": "B"}],
                },
            ]
        }
    }
    result = extract_transitions_from_payload("t1", "X", payload)
    assert [t.to_status for t in result] == ["B", "C"]


def test_same_payload_for_two_tenants_yields_distinct_transitions():
    payload = {
        "changelog": {
            "histories": [
                {
                    "created": "2026-01-01T12:00:00Z",
                    "items": [{"field": "status", "fromString": "A", "toString": "B"}],
                }
            ]
        }
    }
    a = extract_transitions_from_payload("tenant-a", "X", payload)
    b = extract_transitions_from_payload("tenant-b", "X", payload)
    assert a[0].tenant_id == "tenant-a"
    assert b[0].tenant_id == "tenant-b"
