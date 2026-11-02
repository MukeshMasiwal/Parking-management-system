# Runtime Validation Report

**Generated:** 2026-11-02  
**Target:** `Parking-management-system`

## Summary

| Step | Status | Exit Code | Details |
|---|---:|---:|---|
| Database migration | PASS | 0 | Existing database initialized; 5 roles, 5 demo accounts, 50 slots and 3 floors preserved |
| Full regression suite | PASS | 0 | 204 tests passed, 39 subtests passed |
| Runtime workflow suite | PASS | 0 | Security/QR, cashier/payment/receipt, exit, and valet lifecycle flows passed |
| GUI workflow suite | PASS | 0 | 7 existing screens plus admin user-management behavior exercised in the headless Tk fallback |
| Desktop startup | PASS | 0 | `main.py` process remains alive as PID 7272 |
| Browser E2E | N/A | n/a | This is a CustomTkinter desktop application and exposes no HTTP/localhost UI |

**Overall:** PASS

## Role and Authentication Verification

- Exactly five persisted roles are present: `ADMIN`, `MANAGER`, `CASHIER`, `SECURITY`, `VALET`.
- Exactly five supported demo accounts are present; the obsolete `librarian` account is removed by migration.
- All five accounts authenticate with the role-specific bcrypt credentials in the local ignored file [demo_credentials.local.txt](./demo_credentials.local.txt).
- Admin can create a user, assign a supported role, authenticate as that user, and duplicate usernames are rejected.
- Permission mappings verified: admin has full access; manager, cashier, security, and valet have distinct least-privilege permission sets.

## QR and Data Integrity

- QR token generation uses 128 bits of cryptographic entropy.
- QR image generation and OpenCV image decoding both pass.
- Duplicate active vehicle and duplicate active slot constraints remain enforced.
- Payment completion accepts only `PAID` or `EXEMPT`; non-zero exempt payments are rejected.
- Valet transitions are enforced in order and duplicate open assignments are blocked.
- The prior production startup defect caused by creating indexes before migrating `floor_id`/payment columns was fixed.

## Startup Command

From PowerShell:

```powershell
$env:PARKING_DEMO_MODE = "1"
$env:PARKING_DEMO_PASSWORD_ADMIN = "<value from demo_credentials.local.txt>"
$env:PARKING_DEMO_PASSWORD_MANAGER = "<value from demo_credentials.local.txt>"
$env:PARKING_DEMO_PASSWORD_CASHIER = "<value from demo_credentials.local.txt>"
$env:PARKING_DEMO_PASSWORD_SECURITY = "<value from demo_credentials.local.txt>"
$env:PARKING_DEMO_PASSWORD_VALET = "<value from demo_credentials.local.txt>"
python main.py
```

The application is currently running as a desktop process. There is no local URL.

## Remaining Limitations

- Browser automation is not applicable because the product is desktop-only.
- Native window automation was unavailable in this environment; GUI behavior was validated through the repository's headless CustomTkinter test harness.
