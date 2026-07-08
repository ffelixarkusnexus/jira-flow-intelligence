# DATA MODEL RELATIONSHIPS

## Overview

Defines how entities relate to each other.

---

## Relationships

issues (1) → (N) transitions  
issues (1) → (N) time_slices  
issues (1) → (1) metrics_issue  

time_slices → aggregated into → metrics_status_window  

alerts → reference → issues

---

## Key Joins

- transitions.issue_id = issues.id
- time_slices.issue_id = issues.id
- metrics_issue.issue_id = issues.id

---

## Notes

- time_slices is the central analytical table
- all metrics derive from it