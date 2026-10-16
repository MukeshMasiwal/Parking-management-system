# FMEA: Parking Entry, Billing, and Exit

## Purpose and status

Failure Mode and Effects Analysis prioritizes risks in the parking workflow so
mitigations can be assigned before failures affect users. The ratings below are
**initial estimates**, not field measurements. They are informed by the
architecture and test coverage, but have not been statistically validated with
production incident data.

## Rating scale

Each rating is from 1 (best) to 10 (worst).

- **Severity (S):** 1 = negligible inconvenience; 10 = safety, financial, or
  audit-critical impact.
- **Occurrence (O):** 1 = rare/strongly prevented; 10 = frequent or expected
  without controls.
- **Detection (D):** 1 = almost certain to detect before impact; 10 = unlikely
  to detect before impact. A high detection score is therefore worse.
- **RPN:** `Severity × Occurrence × Detection`.

The RPNs are calculated directly from the displayed ratings.

## Analysis

| Process step | Potential failure mode | Effect | Potential cause | Existing controls | S | O | D | RPN | Mitigation action |
|---|---|---|---|---|---:|---:|---:|---:|---|
| Entry validation | Invalid registration accepted | Search, duplicate prevention, and history become unreliable | Incorrect or bypassed validation | Service-layer normalization and validation tests | 7 | 3 | 3 | 63 | Keep validation at the service boundary; add GUI input/error telemetry if field data becomes available |
| Duplicate check | Same vehicle receives two active sessions | Incorrect occupancy and possible billing conflict | Race or missing duplicate constraint | Case-insensitive lookup and partial unique active-vehicle index | 9 | 2 | 2 | 36 | Retain transactional insert and add multi-process stress testing before deployment |
| Capacity check | Entry accepted when no slot is available | Vehicle has no valid parking location | Stale availability or inconsistent slot status | Availability query, capacity tests, partial unique active-slot index | 9 | 2 | 3 | 54 | Add operational reconciliation report for slot/session mismatches |
| Slot assignment | Incompatible or unintended slot selected | Poor utilization or customer dissatisfaction | Preference rules do not match local policy | Vehicle-type preference tests and explicit-slot validation | 6 | 3 | 4 | 72 | Validate slot policy with facility data and add measured allocation metrics |
| Entry persistence | Slot becomes occupied but session is not persisted | Orphaned occupancy and manual recovery | Partial failure during multi-step write | SQLite transaction and database error tests | 9 | 2 | 3 | 54 | Add fault-injection tests around transaction boundaries |
| Exit validation | Exit time precedes entry time | Incorrect fee or corrupted history | Invalid operator/device timestamp | Fixed-timestamp tests and chronology validation | 8 | 2 | 2 | 32 | Keep rejection before database mutation; surface a clear operator message |
| Fee calculation | Wrong duration, grace period, or hourly rate | Customer overcharge/undercharge | Rounding or setting interpretation defect | Central calculation function and boundary-value tests | 10 | 3 | 2 | 60 | Add independent fee examples approved by course/project owner |
| Exit transaction | Session completes but slot remains occupied | Capacity is understated and future entries are blocked | Non-atomic update or unexpected database error | Atomic completion/release transaction and exit tests | 9 | 2 | 3 | 54 | Add restart/recovery reconciliation and transaction fault injection |
| History retention | Completed record is deleted without authorization | Audit and financial traceability are lost | Unsafe delete path or missing reason | Safe-deletion rules, override, and audit-reason tests | 10 | 2 | 2 | 40 | Restrict deletion permissions and periodically review audit logs |
| Database availability | Database path/connection fails | Entry/exit cannot be completed | Missing parent path, permissions, or locked/corrupt file | Explicit operational-error test; SQLite error propagation | 8 | 3 | 4 | 96 | Add user-facing recovery guidance, backup/restore procedure, and health check |

## Priority interpretation

RPN is a prioritization aid, not a probability estimate. The highest initial
estimate is database availability (96), followed by slot assignment (72) and
invalid registration (63). These priorities should be recalculated when real
incident counts, timestamps, and operator observations are available.

## Evidence boundary

The repository records 175 passing automated tests. No production incident
frequency or field detection data was supplied, so occurrence and detection
ratings remain estimates.
