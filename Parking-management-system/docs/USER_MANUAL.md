# Parking Management System — User Manual

> **Version:** 1.0.0 (Release Build)  
> **Quality Goal:** Q10 — Improve Documentation  
> **Course:** Total Quality Management (TQM) / Software Quality Engineering  

---

## Table of Contents
1. [System Overview and Purpose](#1-system-overview-and-purpose)
2. [Prerequisites and System Requirements](#2-prerequisites-and-system-requirements)
3. [Installation and Setup](#3-installation-and-setup)
4. [Application Startup](#4-application-startup)
5. [Interface Architecture and Navigation](#5-interface-architecture-and-navigation)
6. [Dashboard Walkthrough](#6-dashboard-walkthrough)
7. [Vehicle Entry and Slot Allocation](#7-vehicle-entry-and-slot-allocation)
8. [Vehicle Exit and Billing Procedure](#8-vehicle-exit-and-billing-procedure)
9. [Fee Calculation Engine and Charging Rules](#9-fee-calculation-engine-and-charging-rules)
10. [Parking Records Management](#10-parking-records-management)
11. [Parking Slots Visual Map](#11-parking-slots-visual-map)
12. [In-App Help and FAQ Screen](#12-in-app-help-and-faq-screen)
13. [About Page and Developer Configuration](#13-about-page-and-developer-configuration)
14. [Troubleshooting and Error Recovery](#14-troubleshooting-and-error-recovery)

---

## 1. System Overview and Purpose

The **Parking Management System** is a professional desktop application engineered for managing modern parking facility operations. It integrates automated slot allocation, audit-compliant fee calculations, real-time occupancy tracking, historical session preservation, and Statistical Quality Control (SQC) defect logging into a unified CustomTkinter graphical interface.

### Core Architectural Guarantees
- **Strict Separation of Concerns:** The graphical presentation layer contains zero embedded SQL queries and delegates all state transitions to the business logic service layer ([`parking_service.py`](../parking_service.py)).
- **Data Integrity:** ACID-compliant SQLite transactions and partial unique database indexes guarantee that a vehicle can never hold multiple active sessions simultaneously, and no parking slot can be double-booked.
- **Permanent Audit Trail:** Completed and cancelled sessions remain preserved in historical storage. Billed completed records cannot be deleted without administrative confirmation and a documented audit reason.

---

## 2. Prerequisites and System Requirements

- **Operating System:** Windows 10/11, macOS 11+, or Linux (Ubuntu 20.04+ with Tkinter support).
- **Python Version:** Python 3.8 or newer (Python 3.10+ recommended).
- **Display Resolution:** Minimum resolution 1024x768 (Optimal: 1280x800 or higher).
- **Dependencies:**
  - `customtkinter==5.2.2`
  - `tkinter` (bundled with standard Python installations)
  - `sqlite3` (bundled with standard Python installations)

---

## 3. Installation and Setup

1. **Open a terminal** and navigate to the project root directory:
   ```bash
   cd c:\Users\mukes\Desktop\TQM\Parking-management-system
   ```

2. **(Optional but recommended) Create and activate a virtual environment:**
   ```bash
   # Windows PowerShell / CMD
   python -m venv venv
   .\venv\Scripts\activate

   # macOS / Linux
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   python -m pip install -r requirements.txt
   ```

---

## 4. Application Startup

To launch the graphical desktop interface, execute `main.py`:
```bash
python main.py
```

### Automatic Initialization
On initial launch, the application automatically:
1. Detects or creates the SQLite database file (`parking_system.db`).
2. Generates all schema tables (`parking_slots`, `parking_sessions`, `application_settings`, `defects`) and performance indexes.
3. Seeds the default **50 parking slots** across four categories:
   - 20 Standard Car slots (`A-01` to `A-20`)
   - 15 Compact Car slots (`B-01` to `B-15`)
   - 10 Two-Wheeler slots (`C-01` to `C-10`)
   - 5 EV Charging slots (`E-01` to `E-05`)
4. Seeds configurable billing rates and grace period settings into `application_settings`.

---

## 5. Interface Architecture and Navigation

The user interface utilizes a split-pane layout consisting of a fixed left navigation sidebar and a dynamic right content area.

### Sidebar Controls
- **Branding Header:** Displays the application title and system status.
- **Navigation Buttons:**
  - `📊 Dashboard`: Operational KPIs and live parked vehicle table.
  - `🚗 Vehicle Entry`: Vehicle check-in and slot assignment.
  - `🏁 Vehicle Exit`: Checkout, duration calculation, and receipt generation.
  - `📋 Parking Records`: Historical table, filters, permitted updates, and safe deletion.
  - `🅿️ Parking Slots`: Interactive real-time slot occupancy grid.
  - `❓ Help / FAQ`: In-app knowledge base and troubleshooting.
  - `ℹ️ About`: Application provenance, TQM Q10 quality goals, and developer configuration.
- **Appearance Mode Toggle:** Switch dynamically between `System`, `Dark`, and `Light` modes. The UI adjusts both CustomTkinter frames and Treeview palettes immediately.

---

## 6. Dashboard Walkthrough

The **Dashboard** serves as the primary operational command center. All metrics are computed dynamically from active and completed database records.

### Live KPI Cards
1. **Total Capacity:** Displays total facility slots (default: `50 Slots`).
2. **Available Slots:** Count of unassigned slots currently ready for incoming vehicles.
3. **Occupied Slots:** Real-time count of slots with active parking sessions.
4. **Currently Parked:** Count of active vehicles inside the facility.
5. **Completed Sessions:** Total historical vehicles that have successfully completed their stay.
6. **Recorded Revenue:** The exact monetary total collected from completed sessions, clearly labelled as `Recorded Revenue: $X.XX`.

### Facility Utilization Rate
A color-coded visual progress bar displays the current occupancy percentage:
$$\text{Occupancy Rate (\%)} = \left(\frac{\text{Occupied Slots}}{\text{Total Slots}}\right) \times 100$$

### Currently Parked Vehicles Table
- Lists all vehicles currently inside the facility.
- Columns: `Session ID`, `Vehicle Plate`, `Type`, `Slot No`, `Entry Time`, `Current Stay`, and `Owner Name`.
- **Select for Exit Button:** Selecting any row in this table and clicking **"Process Exit for Selected"** immediately loads that vehicle into the **Vehicle Exit** screen with its billing pre-calculated.
- **Refresh Dashboard Button:** Manually re-syncs all metrics and table rows from the database.

---

## 7. Vehicle Entry and Slot Allocation

Use the **Vehicle Entry** screen when a vehicle arrives at the facility entrance.

### Step-by-Step Entry Procedure
1. Click **🚗 Vehicle Entry** in the sidebar.
2. Enter the **Registration Plate Number** (e.g., `MH12-AB-1234`, `DL01XY9999`):
   - Length must be between 3 and 16 characters.
   - Allowed characters: Letters (`A-Z`), digits (`0-9`), spaces, and hyphens (`-`).
   - Inputs are automatically converted to uppercase and trimmed.
3. Select the **Vehicle Classification**:
   - `Car` (Standard passenger vehicles, sedans, SUVs)
   - `Two-Wheeler` (Motorcycles, scooters)
   - `Compact` (Small cars, hatchbacks)
   - `EV` (Electric vehicles requiring charging slots)
   - `Heavy` (Trucks, vans, buses)
4. (Optional) Provide **Owner Name** and **Owner Contact**.
5. Select the **Slot Allocation Mode**:
   - **Auto-Assign Best Available Slot (Recommended):** The allocator selects the most suitable available slot based on category preference order:
     - *Two-Wheeler:* Prefers `Two-Wheeler` $\rightarrow$ `Compact` $\rightarrow$ `Standard`.
     - *EV:* Prefers `EV` $\rightarrow$ `Standard`.
     - *Compact:* Prefers `Compact` $\rightarrow$ `Standard`.
     - *Heavy:* Prefers `Heavy` $\rightarrow$ `Standard`.
     - *Car:* Prefers `Standard` $\rightarrow$ `Compact`.
   - **Specific Slot:** Enables a dropdown listing every currently available slot in the facility for manual operator selection.
6. Click **🚗 Register Vehicle Entry**.

### Confirmation & Permit Ticket
Upon success, the right card generates an official permit summary displaying:
- Permit Session ID
- Normalized Plate Number
- Vehicle Type
- Assigned Slot Number (the slot is atomically transitioned to `Occupied`)
- Official Entry Timestamp
- Owner Information

### Error Scenarios
- **Duplicate Entry:** If the vehicle already has an active session, an error card highlights the existing session ID and occupied slot.
- **Facility Full:** If all 50 slots are occupied, a `ParkingCapacityFullError` message alerts the operator that capacity has been reached.

---

## 8. Vehicle Exit and Billing Procedure

Use the **Vehicle Exit** screen when a parked vehicle departs from the facility.

### Step-by-Step Exit Procedure
1. Click **🏁 Vehicle Exit** in the sidebar.
2. Search for the active vehicle by:
   - Typing the registration plate number into the search box and clicking **🔍 Find** (or pressing `Enter`).
   - OR selecting the vehicle directly from the **Active Sessions Dropdown**.
3. **Review Fee Preview:**
   - The session preview card displays the vehicle type, assigned slot, entry time, current exit time, elapsed duration, billable hours, and calculated base fee.
4. **Apply Operator Discount (Optional):**
   - Enter a discount amount in dollars (e.g., `5.00`).
   - The estimated payable fee updates in real time. Discounts cannot be negative or exceed the calculated base fee.
5. Click **🏁 Process Exit & Release Slot**.

### Official Parking Receipt
Upon checkout, the system:
1. Marks the session status as `COMPLETED` and saves the final fee and exit timestamp.
2. Atomically updates the parking slot status from `Occupied` back to `Available`.
3. Displays a formatted ASCII receipt ticket on screen showing:
   - Receipt Number (`REC-XXXXXX`)
   - Session ID and Plate Number
   - Slot Released back to Available
   - Exact Entry and Exit Timestamps
   - Total Duration (formatted as `Xh YYm`)
   - Billable Hours and Hourly Rate
   - Base Fee, Discount Applied, and Final Recorded Fee
   - Audit Status: `COMPLETED (RECORD PRESERVED)`

---

## 9. Fee Calculation Engine and Charging Rules

All duration and fee computations are performed strictly by the business logic layer according to the approved **Tiered Hourly Charging Policy**.

### Charging Algorithm
```text
1. elapsed_minutes = (exit_time - entry_time) in minutes
2. If elapsed_minutes <= grace_period_mins (default: 15 mins):
       billable_hours = 0
       fee = $0.00
3. Else:
       billable_hours = max(1, ceil(elapsed_minutes / 60.0))
       fee = round(billable_hours * hourly_rate, 2)
```

### Configurable Billing Parameters
| Setting Key | Default Value | Description |
| :--- | :--- | :--- |
| `grace_period_mins` | `15` | Stays $\le 15$ minutes are completely free of charge ($0.00). |
| `rate_car` / `hourly_rate` | `$20.00` | Hourly rate for standard cars, compact cars, and EVs. |
| `rate_two_wheeler` | `$10.00` | Hourly rate for motorcycles, scooters, and bikes. |
| `rate_heavy` | `$40.00` | Hourly rate for heavy transport vehicles, vans, and trucks. |

*Note: Stays exceeding the 15-minute grace period by even 1 minute incur a minimum 1-hour charge and are rounded up to the next full hour.*

---

## 10. Parking Records Management

The **Parking Records** screen provides complete oversight over operational and historical sessions.

### Filtering and Search
- **Search:** Search by vehicle plate number, owner name, or slot number.
- **Status Filter:** Filter by `All`, `ACTIVE`, `COMPLETED`, or `CANCELLED`.
- **Type Filter:** Filter by `All`, `Car`, `Two-Wheeler`, `Compact`, `EV`, or `Heavy`.
- **Reset Button:** Clears all active filters and reloads complete records.

### Permitted Record Operations
Selecting a row in the table unlocks administrative action buttons:

1. **✏️ Update Permitted Info:**
   - Opens an update modal allowing operators to correct `Vehicle Type`, `Owner Name`, and `Owner Contact`.
   - Immutable financial and operational fields (`id`, `vehicle_registration_number`, `slot_id`, `entry_time`, `exit_time`, `fee`, `status`) cannot be modified.

2. **🚫 Cancel Active Entry:**
   - Intended for entry records logged in error (e.g., driver turned around immediately).
   - Re-confirms with the operator, sets session status to `CANCELLED`, and immediately releases the reserved slot back to `Available`.

3. **🗑️ Delete Record Safely:**
   - Enforces strict data-protection and compliance rules:
     - **Active Sessions:** Cannot be deleted directly. The operator is instructed to cancel the session first to avoid orphaned slot reservations.
     - **Paid Completed Sessions ($ > 0.00):** Requires administrative confirmation and a mandatory non-empty **Audit Reason** before deletion.
     - **Zero-Fee or Cancelled Sessions:** Can be safely deleted after standard confirmation.

---

## 11. Parking Slots Visual Map

The **Parking Slots** view renders a visual, color-coded floor plan of all 50 slots in the facility.

### Slot Card Layout
- **Slot Identifier:** Header showing slot code (e.g., `A-01`, `B-05`, `C-02`, `E-01`).
- **Classification Subtitle:** Indicates designated slot type (`Standard`, `Compact`, `Two-Wheeler`, `EV`).
- **Status Indicator Badges:**
  - 🟢 **AVAILABLE (Green):** Slot is empty and ready for incoming vehicles.
  - 🔴 **OCCUPIED (Red):** Displays a red action button with the parked vehicle's license plate. Clicking this button immediately loads the vehicle in the **Vehicle Exit** checkout screen.
  - 🟡 **MAINTENANCE (Gray):** Slot is temporarily out of service.
  - 🔵 **RESERVED (Blue):** Slot is reserved for designated use.

### Controls
- **Filter Type Dropdown:** View only specific slot types (e.g., only EV charging bays).
- **Status Filter Dropdown:** View only Available or Occupied slots.
- **🔄 Sync with Database Button:** Refreshes slot occupancy states against real-time database rows.

---

## 12. In-App Help and FAQ Screen

Access the **❓ Help / FAQ** tab in the sidebar for immediate in-app guidance without leaving the application.

### Features
- **Interactive Search Filter:** Type any keyword into the search bar (`grace`, `duplicate`, `capacity`, `fee`, `error`, `delete`) to filter questions and answers in real time.
- **Categorized Answers:** Covers detailed walkthroughs for:
  - Vehicle Registration procedures
  - Duplicate vehicle prevention rationale
  - Facility capacity thresholds
  - Fee calculation and grace period rules
  - Slot map legend and shortcut actions
  - Step-by-step resolution of common error messages

---

## 13. About Page and Developer Configuration

Access the **ℹ️ About** tab in the sidebar to view project provenance, academic course alignment, and system specifications.

### Displayed Information
- **Application Name & Version:** Parking Management System v1.0.0 (Release Build).
- **Academic Course:** Total Quality Management (TQM) / Software Quality Engineering.
- **Quality Goal:** **Q10 — Improve Documentation** (Interactive guides, verified user manuals, in-app FAQ system, and inline docstrings).
- **Technology Stack:** Python 3, CustomTkinter 5.2.2, SQLite 3, Python unittest.
- **Configurable Developer Placeholders:**
  - Default Name: `[YOUR_NAME_HERE]`
  - Default Registration Number: `[YOUR_REGISTRATION_NO_HERE]`

### Personalizing Developer Information
To customize developer identifiers without editing code:
1. Click **⚙️ Configure Developer Info** in the top-right corner of the About screen.
2. Enter your real Name and Student Registration Number.
3. Click **Save Settings**.
4. The identifiers are saved into the `application_settings` database table and reflected on the About screen.

---

## 14. Troubleshooting and Error Recovery

### Common Scenarios and Solutions

#### 1. "ValidationError: Vehicle registration number must contain at least 3 alphanumeric characters"
- **Cause:** Plate string was left blank or contains fewer than 3 valid alphanumeric characters.
- **Fix:** Enter a valid registration plate between 3 and 16 characters (e.g., `MH12-AB-1234`).

#### 2. "DuplicateActiveSessionError: Vehicle is already parked in slot X"
- **Cause:** An active parking session already exists for this registration plate.
- **Fix:** Verify if the vehicle is currently inside the facility. If the previous exit was missed, navigate to **Vehicle Exit** and process the departure first.

#### 3. "ParkingCapacityFullError: Parking facility is at 100% capacity"
- **Cause:** All 50 slots are marked `Occupied` or `Maintenance`.
- **Fix:** No slots are available. Process exits for departed vehicles to release slots before admitting new cars.

#### 4. "UnsafeDeletionError: Financial audit history must be preserved"
- **Cause:** Attempted to delete a completed session with a recorded fee without administrative credentials or audit reason.
- **Fix:** In the deletion dialog, enter a valid audit justification (e.g., `Audited duplicate record correction #104`) to satisfy compliance rules.

#### 5. "Database Locked" Error
- **Cause:** Another process or SQLite browser tool has an exclusive lock on `parking_system.db`.
- **Fix:** Close external SQLite database viewer utilities and retry the action.

---

*End of User Manual. For technical architecture details, refer to the [Interactive README](../README.md).*
