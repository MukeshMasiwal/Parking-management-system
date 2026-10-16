# Software Requirements Specification: Measurable CTQ Parameters

## Purpose

This SRS section defines Critical to Quality (CTQ) parameters for the Parking
Management System. CTQs translate user expectations into measurable acceptance
criteria that can be verified by automated tests.

## CTQ requirements

| CTQ ID | Critical-to-quality parameter | Measurable acceptance criterion | Verification source |
|---|---|---|---|
| CTQ-01 | Valid vehicle registration | Non-empty registration is normalized to uppercase and accepted only when 3–16 characters contain allowed letters, numbers, spaces, or hyphens; invalid required fields raise `ValidationError` | `tests/test_comprehensive.py` invalid-field tests |
| CTQ-02 | Correct fee calculation | At 15 minutes or less, fee is 0; after the grace period, billable hours use ceiling rounding; default rates are Car $20/hour, Two-Wheeler $10/hour, Heavy $40/hour | `TestDurationAndFeeCalculation` and service calculation logic |
| CTQ-03 | Consistent slot assignment | A valid entry receives one available slot; vehicle-type preference rules are deterministic; an explicitly requested occupied/nonexistent slot is rejected | Slot-assignment and capacity tests |
| CTQ-04 | Duplicate active prevention | A vehicle cannot have more than one `ACTIVE` session, including case/whitespace variants | Partial unique index and duplicate-entry tests |
| CTQ-05 | Capacity enforcement | When no slots are available, a new entry raises `ParkingCapacityFullError`; after exit, the released slot can be assigned again | Capacity and slot-release tests |
| CTQ-06 | Exit integrity | Exit before entry is rejected without releasing the slot; valid exit returns a completed receipt and releases the assigned slot | Exit validation and vehicle-exit tests |
| CTQ-07 | Historical preservation | A completed session remains queryable with entry time, exit time, fee, and `COMPLETED` status | Historical-record tests and `parking_records` view test |
| CTQ-08 | Safe record management | Active records cannot be deleted; billed completed records require administrative override and a nonblank audit reason | Safe-deletion tests |
| CTQ-09 | Database error visibility | An unusable database path raises an explicit operational error; no fallback database write is permitted | Database error-handling regression test |

## Quality evidence and limits

The current evidence is the final automated result: 175 `unittest` tests
passed with exit code 0. These CTQs are not production service-level
measurements. Response-time, field defect frequency, and operational
availability targets require future instrumentation and observations.
