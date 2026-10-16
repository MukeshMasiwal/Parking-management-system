# Fishbone Diagram: Unexpected Vehicle Entry Rejection

## Purpose

A fishbone (Ishikawa) diagram organizes possible causes of a quality problem.
It prevents the team from jumping directly to a single root cause. The
categories required for this analysis are People, Process, Software Code, and
Infrastructure.

## Problem statement

**A valid vehicle entry is unexpectedly rejected or is not completed.**

This is a quality-risk problem statement, not a claim that the defect was
observed in the final test run. The final automated run recorded 175 passing
tests and no functional defects.

```text
                                      Unexpected valid-entry rejection
                                                   |
        People -----------------------\            |            /---------------- Infrastructure
        - operator input error         \           |           /             - unavailable DB path
        - unclear error message         \          |          /              - file permissions/locking
        - insufficient training          \         |         /               - damaged SQLite file
                                          \        |        /
        Process ---------------------------\-------+-------/---------------- Software Code
        - entry policy differs locally     \      |      /                  - validation boundary defect
        - slot policy not documented        \     |     /                   - stale availability decision
        - no recovery/reconciliation step    \    |    /                    - transaction failure
```

## Cause register

| Category | Possible cause | Status | How to verify |
|---|---|---|---|
| People | Operator enters a malformed or incomplete registration | Hypothesis | Review rejected-entry messages and conduct supervised usability observation |
| People | Operator cannot interpret a capacity or database error | Hypothesis | Observe task completion and interview operators |
| Process | Local slot compatibility policy differs from the implemented preference order | Hypothesis | Compare approved operating procedure with `find_best_available_slot` rules |
| Process | No documented recovery step exists after a database error | Hypothesis | Review operating procedure and simulate an unavailable database path |
| Software Code | Registration normalization rejects a valid local format | Hypothesis | Add approved-format boundary cases to the automated suite |
| Software Code | Slot availability changes between selection and persistence | Hypothesis | Run controlled concurrent/multi-process tests |
| Infrastructure | Database directory is unavailable or not writable | Testable risk | Run the database-path error test and test permissions in a controlled environment |
| Infrastructure | SQLite file is locked or corrupted | Hypothesis | Controlled fault-injection and backup/restore test |

## Evidence boundary

Only the test result is established: the final `unittest` discovery command
completed with exit code 0 and 175 tests passing. All listed causes remain
hypotheses or testable risks until evidence is collected.
