# Technical Architecture

## Purpose

Defines how the system ingests, processes, and transforms Jira data into actionable insights.

---

## What This Layer Solves

- How data enters the system (Jira API)
- How it is normalized (transitions)
- How it becomes analyzable (time slices)
- How it flows into metrics and insights

---

## Files

- `01_system_overview.md`  
  → High-level architecture and component responsibilities

- `02_jira_api_integration.json`  
  → Real Jira API payload example

- `02_jira_api_integration.py`  
  → Transition extraction logic from changelog

- `03_data_pipeline_design.md`  
  → End-to-end data pipeline (ingestion → insights)

---

## Key Principle

The system is **pipeline-driven**, not request-driven.

All insights depend on:
→ accurate historical reconstruction of work

---

## Critical Dependency

Everything downstream depends on:
👉 correct extraction of status transitions