def test_time_slices():
    issue = {
        "fields": {
            "created": "2026-01-01T10:00:00Z",
            "status": {"name": "Done"}
        }
    }

    transitions = [
        {"from": "In Progress", "to": "Review", "timestamp": "2026-01-01T12:00:00Z"},
        {"from": "Review", "to": "Done", "timestamp": "2026-01-01T14:00:00Z"}
    ]

    slices = build_time_slices(issue, transitions)

    assert slices[0]["duration"] == 7200  # 2h
    assert slices[1]["duration"] == 7200  # 2h
