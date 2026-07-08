# Alerting System

## Purpose

Notify when work deviates from expected behavior.

---

## Role in System

- Complements insights
- Enables proactive intervention
- Operates at issue-level and system-level

---

## Files

- `01_alert_definitions.md`  
  → Types of alerts and rules

- `02_alert_engine_design.py`  
  → Evaluation logic

- `03_alert_examples.md`  
  → Real-world scenarios

---

## Design Principles

- Deterministic triggers
- No duplicates
- Configurable thresholds

---

## Key Difference

Insights:
→ explain system behavior

Alerts:
→ signal actionable events