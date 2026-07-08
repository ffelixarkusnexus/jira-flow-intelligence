def extract_transitions(issue):
    transitions = []

    for history in issue["changelog"]["histories"]:
        ts = history["created"]

        for item in history["items"]:
            if item["field"] == "status":
                transitions.append({
                    "from": item["fromString"],
                    "to": item["toString"],
                    "timestamp": ts
                })

    return sorted(transitions, key=lambda x: x["timestamp"])