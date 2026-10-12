# Parking Management System

A desktop application for managing parking operations, built with Python, CustomTkinter, and SQLite3.

## Technologies Used
- **Language**: Python 3
- **GUI Framework**: CustomTkinter
- **Database**: SQLite3
- **Charting**: Matplotlib
- **Testing**: unittest

## Parking business rules

The service layer in [`parking_service.py`](parking_service.py) owns parking
workflow rules and uses the database layer for persistence:

- Registration numbers are trimmed, uppercased, and validated before entry.
- A vehicle can have only one active session, and each active session occupies
  one slot. SQLite transactions and unique partial indexes protect assignments
  during repeated or concurrent requests.
- Entry automatically selects the first suitable available slot. Completed
  sessions remain in history.
- Active sessions cannot be deleted. A session entered in error must be
  cancelled first; paid completed sessions require an administrative override
  and an audit reason before deletion.
- Exit updates the session and releases its slot in one database transaction.
  Invalid exits leave the active session and occupied slot unchanged.

### Approved fee policy

The hourly rates and grace period are configurable in the
`application_settings` table:

- 15-minute grace period: stays of 15 minutes or less cost `$0.00`.
- After the grace period, charge a minimum of one hour.
- Round elapsed time up to the next whole hour.
- Default rates are `$20/hour` for cars, `$10/hour` for two-wheelers, and
  `$40/hour` for heavy vehicles. Rates can be changed with
  `database.set_setting`.

No GUI code is required to use the service layer. The executable checks are in
[`tests/test_parking_service.py`](tests/test_parking_service.py).

## Setup Instructions

1. **Clone the repository:**
   ```bash
   git clone <repository-url>
   cd Parking-management-system
   ```

2. **Set up a virtual environment (optional but recommended):**
   ```bash
   python -m venv venv
   # On Windows
   venv\Scripts\activate
   # On macOS/Linux
   source venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Launch the application:**
   ```bash
   python main.py
   ```
