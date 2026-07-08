def evaluate_alerts(issue, slices, rules):
    alerts = []

    for rule in rules:

        if rule["type"] == "status_duration":
            for s in slices:
                if s["status"] == rule["status"]:
                    if s["duration"] > rule["threshold_seconds"]:
                        alerts.append({
                            "issue_id": issue["id"],
                            "status": s["status"],
                            "type": "duration_exceeded"
                        })

    return alerts