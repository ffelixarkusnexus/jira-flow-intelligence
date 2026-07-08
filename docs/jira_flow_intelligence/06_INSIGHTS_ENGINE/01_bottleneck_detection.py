def detect_bottleneck(status_metrics, previous_metrics):
    best = None

    for status, current in status_metrics.items():
        prev = previous_metrics.get(status)

        if not prev:
            continue

        score = 0

        time_ratio = current["avg"] / prev["avg"]
        wip_ratio = current["wip"] / prev["wip"]
        throughput_delta = (current["throughput"] - prev["throughput"]) / prev["throughput"]

        if time_ratio >= 1.3:
            score += 2

        if wip_ratio >= 1.2:
            score += 1

        if throughput_delta <= -0.2:
            score += 1

        if score >= 3:
            if not best or score > best["score"]:
                best = {
                    "status": status,
                    "score": score,
                    "time_ratio": time_ratio,
                    "wip_ratio": wip_ratio,
                    "throughput_delta": throughput_delta
                }

    return best