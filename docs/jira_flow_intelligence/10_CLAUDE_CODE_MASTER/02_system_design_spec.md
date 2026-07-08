# SYSTEM DESIGN SPEC

## Services

- ingestion_service
- transition_service
- slicing_service
- metrics_service
- insight_service
- alert_service

---

## Responsibilities

Each service must:
- be stateless
- be testable independently

---

## Data Flow Contracts

- ingestion → raw JSON
- transitions → normalized rows
- slices → computed durations
- metrics → aggregated values