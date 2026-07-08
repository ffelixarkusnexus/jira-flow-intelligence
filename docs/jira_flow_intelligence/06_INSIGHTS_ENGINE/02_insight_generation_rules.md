# INSIGHT GENERATION RULES

## Objective

Convert computed signals into structured insights.

---

## Input

- time_ratio
- wip_ratio
- throughput_delta
- score

---

## Rules

IF score >= 3:
  → status is bottleneck

---

## Reason Mapping

IF time_ratio >= 1.3:
  → "Average time increased X%"

IF wip_ratio >= 1.2:
  → "WIP increased X%"

IF throughput_delta <= -0.2:
  → "Throughput decreased X%"

---

## Output Structure

{
  "status": "Review",
  "score": 4,
  "reasons": [
    "Average time increased 40%",
    "WIP increased 25%"
  ]
}