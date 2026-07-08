# METRICS COMPUTATION — EXACT

## 1. CYCLE TIME

cycle_seconds =
  CASE
    WHEN done_at IS NOT NULL
    THEN done_at - created_at
    ELSE NOW() - created_at
  END

---

## 2. TIME IN STATUS

SELECT status, SUM(duration_seconds)
FROM time_slices
GROUP BY status

---

## 3. ACTIVE VS WAIT TIME

CONFIG:
active_statuses = ["In Progress", "Review"]

active_seconds =
  SUM(duration_seconds WHERE status IN active_statuses)

wait_seconds =
  cycle_seconds - active_seconds

---

## 4. WIP (WORK IN PROGRESS)

WIP at time T =
  COUNT(issues WHERE created_at <= T AND (done_at IS NULL OR done_at > T))

---

## 5. THROUGHPUT

Throughput(window) =
  COUNT(issues WHERE done_at BETWEEN window_start AND window_end)

---

## 6. STATUS METRICS (WINDOW)

For each status:

avg_seconds =
  AVG(duration_seconds)

p50_seconds =
  PERCENTILE_CONT(0.5)

p90_seconds =
  PERCENTILE_CONT(0.9)

---

## 7. BASELINES

Baseline =
  previous_window metrics

Used for:
- time_ratio
- wip_ratio
- throughput_delta