from datetime import datetime

def build_time_slices(issue, transitions):
    slices = []

    created_at = parse(issue["fields"]["created"])
    prev_time = created_at

    if transitions:
        prev_status = transitions[0]["from"]
    else:
        prev_status = issue["fields"]["status"]["name"]

    for t in transitions:
        current_time = parse(t["timestamp"])

        duration = (current_time - prev_time).total_seconds()

        slices.append({
            "status": prev_status,
            "start": prev_time,
            "end": current_time,
            "duration": duration
        })

        prev_time = current_time
        prev_status = t["to"]

    # final slice
    if prev_status == "Done":
        end_time = prev_time
    else:
        end_time = datetime.utcnow()

    duration = (end_time - prev_time).total_seconds()

    slices.append({
        "status": prev_status,
        "start": prev_time,
        "end": end_time,
        "duration": duration
    })

    return slices