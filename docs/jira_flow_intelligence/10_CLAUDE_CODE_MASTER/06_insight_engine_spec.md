# INSIGHT ENGINE — PRODUCTION LOGIC

## GOAL

Identify:
- bottleneck stage
- reason(s)
- confidence

---

## INPUT

For each status (per time window):

- avg_time_seconds
- previous_avg_time_seconds
- avg_wip
- previous_avg_wip
- throughput
- previous_throughput

---

## DERIVED SIGNALS

time_ratio = avg_time / previous_avg_time
wip_ratio = avg_wip / previous_avg_wip
throughput_delta = (throughput - previous) / previous

---

## SCORING MODEL

score = 0

IF time_ratio >= 1.3:
  score += 2

IF wip_ratio >= 1.2:
  score += 1

IF throughput_delta <= -0.2:
  score += 1

IF time_ratio >= 1.5:
  score += 1 (extra weight)

---

## BOTTLENECK DECISION

IF score >= 3:
  status = BOTTLENECK

Pick highest score across statuses

---

## CONFIDENCE

score 3 → medium  
score 4 → high  
score 5+ → very high  

---

## OUTPUT STRUCTURE

{
  "status": "Review",
  "score": 5,
  "confidence": "high",
  "reasons": [
    "Average time increased 42%",
    "WIP increased 30%",
    "Throughput decreased 25%"
  ]
}

---

## NATURAL LANGUAGE LAYER

AI INPUT:
- structured output above

AI OUTPUT:
"Review is the current bottleneck due to increased wait time, a growing queue, and reduced throughput."

---

## CRITICAL RULE

AI MUST NOT:
- invent numbers
- override logic

It only translates structured facts → language