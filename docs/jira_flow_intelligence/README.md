# Jira Flow Intelligence

> We don’t track work.  
> We reveal where your delivery system is breaking.

---

## 🧠 Overview

Jira Flow Intelligence is a specialized system (initially delivered as a Jira plugin) focused on:

- Detecting bottlenecks automatically
- Explaining why work is slowing down
- Providing actionable insights (not just dashboards)
- Enabling teams to improve flow without blame

This repository contains **complete product, technical, and AI execution documentation** required to build and scale the system.

---

## 🚀 Tech Stack (Opinionated)

- **Backend:** Python + FastAPI  
- **Frontend:** Next.js + TailwindCSS  
- **Infra:** AWS (Lambda / ECS / RDS / S3)  
- **Data Processing:** Python (batch + async workers)  
- **AI Layer:** LLM-based insight generation (Claude / GPT)  

---

## 📂 Documentation Structure

---

### 🧭 Executive & Strategy

- [Vision & Positioning](./00_EXECUTIVE_OVERVIEW/01_vision_and_positioning.md)  
  → Core idea, differentiation, and why this product wins

---

### 🎯 Product Strategy

- [Product Principles](./01_PRODUCT_STRATEGY/01_product_principles.md)  
  → Non-negotiables and design philosophy

---

### 📚 Domain Knowledge

- [Flow Fundamentals](./02_DOMAIN_KNOWLEDGE/01_flow_fundamentals.md)  
  → Core system dynamics (queues, bottlenecks, flow)

---

### 🏗️ Technical Architecture

- [System Overview](./03_TECHNICAL_ARCHITECTURE/01_system_overview.md)  
  → High-level architecture

- [Jira API Integration (Payload)](./03_TECHNICAL_ARCHITECTURE/02_jira_api_integration.json)  
  → Real API response examples

- [Jira API Integration (Parser)](./03_TECHNICAL_ARCHITECTURE/02_jira_api_integration.py)  
  → Transition extraction logic

- [Data Pipeline Design](./03_TECHNICAL_ARCHITECTURE/03_data_pipeline_design.md)  
  → Ingestion → processing → insights flow

---

### 🧱 Data Model

- [Core Entities](./04_DATA_MODEL/01_core_entities.md)  
- [Derived Metrics](./04_DATA_MODEL/02_derived_metrics.md)  
- [Relationships](./04_DATA_MODEL/03_data_model_relationships.md)  

---

### ⚙️ Features

- [MVP Features](./05_FEATURE_SPECS/01_mvp_features.md)  
- [Advanced Features](./05_FEATURE_SPECS/02_advanced_features.md)  

---

### 🧠 Insights Engine (CORE DIFFERENTIATOR)

- [Bottleneck Detection (Code)](./06_INSIGHTS_ENGINE/01_bottleneck_detection.py)  
- [Insight Rules](./06_INSIGHTS_ENGINE/02_insight_generation_rules.md)  
- [Trend Analysis](./06_INSIGHTS_ENGINE/03_trend_analysis.md)  

---

### 🚨 Alerting System

- [Alert Definitions](./07_ALERTING_SYSTEM/01_alert_definitions.md)  
- [Alert Engine (Code)](./07_ALERTING_SYSTEM/02_alert_engine_design.py)  
- [Examples](./07_ALERTING_SYSTEM/03_alert_examples.md)  

---

### 🎨 UX / UI

- [Dashboard Design](./08_UX_UI_GUIDELINES/01_dashboard_design.md)  
- [Insight-First UI](./08_UX_UI_GUIDELINES/02_insight_first_ui.md)  
- [User Flows](./08_UX_UI_GUIDELINES/03_user_flows.md)  

---

### 🤖 AI Layer

- [AI Roles](./09_AI_AGENT_SPECS/01_ai_roles_and_responsibilities.md)  
- [Prompt Engineering](./09_AI_AGENT_SPECS/02_prompt_engineering.md)  
- [Examples](./09_AI_AGENT_SPECS/03_insight_generation_examples.md)  

---

## 🚨 Claude Code Execution (MOST IMPORTANT)

This folder is designed for **direct implementation with minimal ambiguity**.

- [Master Instructions](./10_CLAUDE_CODE_MASTER/01_master_instructions.md)  
- [System Design Spec](./10_CLAUDE_CODE_MASTER/02_system_design_spec.md)  
- [Backend Plan](./10_CLAUDE_CODE_MASTER/03_backend_implementation_plan.md)  
- [Frontend Plan](./10_CLAUDE_CODE_MASTER/04_frontend_implementation_plan.md)  
- [Data Processing Logic (Spec)](./10_CLAUDE_CODE_MASTER/05_data_processing_logic.md)  
- [Data Processing Logic (Code)](./10_CLAUDE_CODE_MASTER/05_data_processing_logic.py)  
- [Insight Engine Spec](./10_CLAUDE_CODE_MASTER/06_insight_engine_spec.md)  
- [Iteration Plan](./10_CLAUDE_CODE_MASTER/07_iteration_plan.md)  
- [Testing & Validation](./10_CLAUDE_CODE_MASTER/08_testing_and_validation.py)  
- [Definition of Done](./10_CLAUDE_CODE_MASTER/09_definition_of_done.md)  

👉 This is the **primary entry point for building the system**

---

### 🗺️ Roadmap

- [Phase 1](./11_ROADMAP_AND_ITERATIONS/01_phase_1.md)  
- [Phase 2](./11_ROADMAP_AND_ITERATIONS/02_phase_2.md)  
- [Phase 3](./11_ROADMAP_AND_ITERATIONS/03_phase_3.md)  
- [Phase 4](./11_ROADMAP_AND_ITERATIONS/04_phase_4.md)  

---

### 📎 Appendix

- [Glossary](./13_APPENDIX/01_glossary.md)  
- [Formulas](./13_APPENDIX/02_formulas_and_definitions.md)  
- [Sample Insights](./13_APPENDIX/03_sample_insights_library.md)  

---

## 🧭 How to Use This Documentation

### For Product / Strategy
Start with:
1. Vision & Positioning  
2. Product Principles  
3. Domain Knowledge  

---

### For Engineers
Start with:
1. System Overview  
2. Data Model  
3. Data Processing Logic  

---

### For Claude Code (Execution)

Start with:

👉 `10_CLAUDE_CODE_MASTER/01_master_instructions.md`

Then follow the build order strictly.

---

## ⚠️ Critical Implementation Principle

- All computations must be **deterministic**
- Changelog is the **single source of truth**
- AI is used **only for explanation, never logic**

---

## 🏁 Final Note

This is not a generic Jira plugin.

This is a **Flow Intelligence System** designed to become:

> The default way teams understand where their delivery breaks.