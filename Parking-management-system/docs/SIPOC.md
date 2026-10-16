# SIPOC: Vehicle Entry and Exit

## Purpose

SIPOC (Suppliers, Inputs, Process, Outputs, Customers) gives a high-level view
of the parking transaction workflow. It is useful for BBAT104 because it makes
the hand-offs and quality requirements visible before detailed process analysis.

## Scope and architecture

The workflow is implemented by the desktop GUI, `parking_service.py`, and
`database.py`. SQLite stores parking slots, active/completed sessions, settings,
and defect records. The service layer validates inputs and performs the
transactional entry/exit operations; the GUI presents the results to an
operator.

## SIPOC table

| Suppliers | Inputs | Process | Outputs | Customers |
|---|---|---|---|---|
| Vehicle owner/driver | Registration number, vehicle type, owner details | 1. Receive entry request | Validated entry request or explicit validation error | Parking operator |
| Parking operator | Entry action and optional slot choice | 2. Validate registration, vehicle type, and timestamp | Normalized registration and vehicle type | Service layer/database |
| Application settings | Grace-period and hourly-rate settings | 3. Check duplicate active vehicle and available capacity | Duplicate/capacity decision | Vehicle owner, operator |
| Parking slot configuration | Slot types and availability status | 4. Select a compatible available slot | Active session with assigned slot | Vehicle owner, operator |
| SQLite database | Current slots and sessions | 5. Atomically reserve the slot and persist the active session | Entry confirmation and occupied slot | Operator, audit/history users |
| Vehicle owner/driver | Vehicle presented for departure and exit request | 6. Locate the active session by ID or registration | Active session or session-not-found error | Parking operator |
| Parking operator | Exit action, controlled exit timestamp, optional discount | 7. Validate exit chronology and calculate duration/fee | Receipt with duration, rate, fee, and status | Vehicle owner, operator |
| SQLite transaction layer | Active session and assigned slot | 8. Atomically complete the session and release the slot | Completed historical record and available slot | Management, audit/history users |

## Quality characteristics at the boundary

- **Accuracy:** fee calculation uses a 15-minute grace period, ceiling-hour
  billing, and configured rates.
- **Validity:** registration, vehicle type, timestamps, and discount inputs are
  validated before persistence.
- **Integrity:** partial unique indexes prevent duplicate active vehicles and
  double-booked active slots.
- **Traceability:** completed sessions remain in history and billed records
  require an administrative reason before deletion.
- **Isolation:** automated tests use temporary SQLite databases and fixed
  timestamps, so real application records are not changed.

## Verification

Run the automated suite from the repository root:

```powershell
python -m unittest discover -s tests -v
```

The recorded result for this project is 175 tests passing with exit code 0.
