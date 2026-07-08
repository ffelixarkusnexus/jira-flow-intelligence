# DATA PIPELINE DESIGN

## Overview

This system processes Jira data through a deterministic pipeline:

Jira API → Raw Storage → Transition Extraction → Time Slices → Metrics → Insights → Alerts

---

## 1. Ingestion Stage

- Fetch issues with changelog
- Store raw JSON (for audit/debugging)

Requirements:
- Pagination support
- Retry with exponential backoff
- Rate limit handling

---

## 2. Normalization Stage

Input: raw Jira JSON  
Output: transitions table

Steps:
- Extract status transitions
- Deduplicate (issue_id, timestamp, to_status)
- Persist to DB

---

## 3. Time Slice Computation (CRITICAL)

Input: transitions  
Output: time_slices

- Build sequential durations per status
- Ensure no overlaps
- Ensure full coverage from created → now/done

---

## 4. Metrics Aggregation

Levels:
- Per issue
- Per status
- Per time window

---

## 5. Insight Generation

- Compute signals
- Apply scoring
- Select bottleneck

---

## 6. Alert Evaluation

- Evaluate rules against current state
- Emit alerts (idempotent)

---

## Scheduling

- Initial full sync
- Incremental sync every N minutes
- Recompute only affected issues

---

## Idempotency Rule

Running the pipeline multiple times MUST:
- produce identical results
- not duplicate rows