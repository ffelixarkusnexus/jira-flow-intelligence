# SYSTEM OVERVIEW — COMPLETE

## Architecture Style

- Modular monolith (initially)
- Event-capable (future)
- Batch + near-real-time hybrid

---

## Components (Detailed)

### 1. Ingestion Service
- Pulls Jira issues + changelogs
- Handles pagination, retries, rate limits
- Stores raw JSON (auditability)

---

### 2. Transition Service
- Extracts status transitions
- Deduplicates
- Persists normalized transitions

---

### 3. Slicing Service (CRITICAL)
- Builds time slices
- Handles edge cases
- Ensures idempotency

---

### 4. Metrics Service
- Aggregates:
  - per issue
  - per status
  - per time window

---

### 5. Insight Service
- Computes signals
- Applies scoring model
- Produces structured insights

---

### 6. Alert Service
- Evaluates rule engine
- Emits alerts
- Avoids duplicates

---

### 7. API Layer
- FastAPI
- Stateless
- Query-based

---

### 8. Frontend
- Next.js
- Insight-first UI

---

## Data Flow (Explicit)

Jira API
  ↓
Ingestion Service
  ↓
Raw Storage
  ↓
Transition Extraction
  ↓
Time Slice Builder
  ↓
Metrics Aggregation
  ↓
Insight Engine
  ↓
API
  ↓
Frontend