# Data Model

## Purpose

Defines the canonical representation of:

- Jira issues
- Status transitions
- Time slices
- Metrics

---

## Why This Matters

This layer guarantees:

- Deterministic computation
- Reproducibility
- Correctness of insights

If this is wrong → everything is wrong.

---

## Files

- `01_core_entities.md`  
  → Database tables and schema

- `02_derived_metrics.md`  
  → Exact formulas for metrics

- `03_data_model_relationships.md`  
  → How entities connect

---

## Core Concept

👉 `time_slices` is the most important table

Everything derives from:
- transitions → slices → metrics → insights

---

## Non-Negotiable Rule

All time-based calculations must originate from:
👉 changelog-derived transitions