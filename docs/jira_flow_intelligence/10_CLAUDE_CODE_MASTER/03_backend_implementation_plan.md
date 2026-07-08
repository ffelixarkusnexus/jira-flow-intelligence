# BACKEND IMPLEMENTATION — FASTAPI

## PROJECT SETUP

pip install fastapi uvicorn sqlalchemy psycopg2

---

## DATABASE TABLES

- issues
- transitions
- time_slices
- metrics_status
- metrics_issue
- alerts

---

## ENDPOINTS

POST /sync
GET /metrics
GET /insights
GET /alerts

---

## SYNC FLOW

1. Fetch issues with changelog
2. Store raw JSON
3. Extract transitions
4. Upsert transitions
5. Recompute slices for affected issues

---

## PERFORMANCE RULES

- Batch inserts (100–500 rows)
- Index:
  - issue_id
  - timestamp
  - status

- Recompute only changed issues