# TREND ANALYSIS

## Objective

Detect changes between time windows.

---

## Windows

- current_window (e.g., last 7 days)
- previous_window

---

## Metrics Compared

- avg_time
- WIP
- throughput

---

## Computation

ratio = current / previous

---

## Thresholds

IF ratio >= 1.2:
  → significant increase

IF ratio <= 0.8:
  → significant decrease

---

## Output Example

{
  "metric": "cycle_time",
  "change": "+25%",
  "direction": "worsening"
}