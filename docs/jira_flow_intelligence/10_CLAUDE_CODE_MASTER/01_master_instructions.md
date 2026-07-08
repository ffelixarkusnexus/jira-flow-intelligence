# MASTER INSTRUCTIONS — JIRA FLOW INTELLIGENCE

## OBJECTIVE

Build a production-grade system that:

1. Computes exact time-in-status from Jira changelogs
2. Derives flow metrics (cycle time, WIP, throughput)
3. Detects bottlenecks using multi-signal scoring
4. Generates deterministic insights
5. Enhances insights with AI (text only)
6. Triggers alerts based on rules

---

## CORE PRINCIPLES (NON-NEGOTIABLE)

1. SOURCE OF TRUTH = CHANGELOG
   - NEVER rely on current status only
   - ALL time calculations must come from transitions

2. IDEMPOTENCY
   - Running processing twice must produce identical results

3. DETERMINISTIC FIRST
   - AI is only for explanation, never for computation

4. SYSTEM > INDIVIDUAL
   - Aggregate by status/stage by default
   - Individual-level only optional

---

## BUILD ORDER (STRICT — DO NOT SKIP)

STEP 1 — Backend skeleton (FastAPI)
STEP 2 — Jira ingestion (OAuth + sync)
STEP 3 — Transition extraction (normalize data)
STEP 4 — Time-slice computation (CRITICAL)
STEP 5 — Metrics aggregation
STEP 6 — Bottleneck detection engine
STEP 7 — Alert engine
STEP 8 — Frontend (insight-first UI)
STEP 9 — AI explanation layer

---

## PROJECT STRUCTURE (ENFORCE)

backend/
  app/
    main.py
    config.py
    db/
      session.py
      models.py
    services/
      ingestion_service.py
      transition_service.py
      slicing_service.py
      metrics_service.py
      insight_service.py
      alert_service.py
    routers/
      sync.py
      metrics.py
      insights.py
      alerts.py

frontend/
  app/
    dashboard/
  components/
    InsightCard.tsx
    BottleneckPanel.tsx
    AlertsList.tsx

---

## FAILURE MODES (MUST HANDLE)

IF changelog is incomplete:
  → fallback to created_at as start

IF issue has no Done:
  → treat as active, end = NOW

IF duplicate transitions:
  → dedupe by (issue_id, timestamp, to_status)

IF transitions out of order:
  → sort strictly by timestamp ASC

---

## SUCCESS CRITERIA

Given the same dataset:
- Metrics must be identical across runs
- Bottleneck must be reproducible
- Insight must be explainable from raw data