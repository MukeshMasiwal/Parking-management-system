# PDCA Log: Database Error-Handling Regression Coverage

## Purpose

PDCA (Plan, Do, Check, Act) records a controlled quality improvement cycle.
This cycle addresses a quality gap identified during test review: database
error handling needed an explicit regression test for an unusable database
location. It is a test-process improvement, not a claim of a production
incident.

## Plan

- **Problem:** Before this cycle, the comprehensive suite did not explicitly
  verify that an unusable database path surfaces an operational error instead
  of silently falling back to another database.
- **Risk:** Entry/exit operations could fail opaquely or operators could be
  misled about persistence.
- **Planned change:** Add a temporary-database test that passes a path whose
  parent directory does not exist and asserts `sqlite3.OperationalError`.
- **Baseline:** No numeric baseline is claimed; the missing regression case was
  identified by test review.

## Do

- Added `test_unusable_database_path_surfaces_operational_error` to
  `tests/test_comprehensive.py`.
- The test uses a path derived from the per-test temporary database name.
- It does not open or modify `parking_system.db` or any application record.
- Fixed timestamps and temporary databases remain the standard test controls.

## Check

Evidence from the final automated run:

```text
Command: python -m unittest discover -s tests -v
Result: Ran 175 tests in 54.405s
Status: OK
Exit code: 0
```

The new database-error-handling test passed as part of that run. This is a
post-improvement test result, not a measured production failure rate.

## Act

- Retain the regression test in the canonical `unittest` discovery path.
- Keep database errors explicit; do not add fallback writes to the real
  application database.
- Future cycle: add controlled permission/locking and backup-restore tests,
  then record measurements only after those tests or operational observations
  exist.
- Recalculate FMEA occurrence/detection estimates when incident evidence is
  available.
