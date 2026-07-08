# DATA PROCESSING — EXACT ALGORITHM

## INPUT

Jira Issue JSON with:
- created timestamp
- changelog histories (status transitions)

---

## STEP 1 — EXTRACT STATUS TRANSITIONS

From changelog:
- Filter items where field = "status"

For each:
- capture:
  - from_status
  - to_status
  - timestamp

---

## STEP 2 — SORT

Sort transitions by timestamp ASC

---

## STEP 3 — INITIALIZE

prev_time = issue.created
prev_status = FIRST KNOWN STATUS

IF first transition exists:
  prev_status = transition[0].from_status
ELSE:
  prev_status = issue.current_status

---

## STEP 4 — BUILD TIME SLICES

FOR each transition T:

  current_time = T.timestamp

  duration = current_time - prev_time

  CREATE slice:
    issue_id
    status = prev_status
    start = prev_time
    end = current_time
    duration_seconds

  UPDATE:
    prev_time = current_time
    prev_status = T.to_status

---

## STEP 5 — FINAL SLICE

IF issue is DONE:
  end_time = done_timestamp
ELSE:
  end_time = NOW

duration = end_time - prev_time

CREATE slice:
  status = prev_status
  start = prev_time
  end = end_time

---

## EDGE CASES (MANDATORY)

### 1. REOPENED ISSUES
- Continue accumulating time
- DO NOT reset cycle time

### 2. STATUS LOOPS
Example:
  In Progress → Review → In Progress

→ Treat each occurrence separately
→ Aggregate later

### 3. MISSING TRANSITIONS
- If first transition is missing:
  → assume created_at → first known status

### 4. PARALLEL STATUSES (RARE)
- Ignore unless explicitly modeled
- Treat as linear

---

## OUTPUT TABLE

time_slices:

- issue_id
- status
- start_timestamp
- end_timestamp
- duration_seconds

---

## VALIDATION TEST

Given:
- Created: 10:00
- Review at 12:00
- Done at 14:00

Expect:
- In Progress: 2h
- Review: 2h