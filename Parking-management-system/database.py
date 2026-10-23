"""
Database Layer for Parking Management System
============================================

This module provides the persistent SQLite data access layer for the
Parking Management System, integrating operational parking session management,
slot tracking, application settings, and TQM defect logging.

Key Features & Guarantees:
- ACID Transactions for multi-step updates (e.g. entry/exit slot state sync).
- Parameterized SQL queries to prevent SQL injection.
- Database constraints & partial unique indexes enforcing:
    1. Maximum 1 active parking session per vehicle.
    2. Maximum 1 active occupant per parking slot.
- Non-destructive initialization: preserves existing data.
- Automated schema initialization on first launch or connection.
- Complete audit trail: completed sessions remain preserved permanently.
"""

import os
import sqlite3
import math
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple

# Default database file path placed in the project root directory
DEFAULT_DB_FILE = "parking_system.db"
DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), DEFAULT_DB_FILE)

# Datetime format used throughout the application
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


# ============================================================================
# Custom Exception Hierarchy
# ============================================================================

class DatabaseError(Exception):
    """Base exception for all database-related errors."""
    pass


class SlotNotAvailableError(DatabaseError):
    """Raised when an operation attempts to occupy an unavailable slot."""
    pass


class ActiveSessionExistsError(DatabaseError):
    """Raised when an active parking session already exists for a vehicle."""
    pass


class SessionNotFoundError(DatabaseError):
    """Raised when a referenced parking session does not exist."""
    pass


class SlotNotFoundError(DatabaseError):
    """Raised when a referenced parking slot does not exist."""
    pass


class InvalidOperationError(DatabaseError):
    """Raised when an invalid state transition or operation is attempted."""
    pass


# ============================================================================
# Schema Definition
# ============================================================================

SCHEMA_SQL = """
-- 1. Parking Slots Table
CREATE TABLE IF NOT EXISTS parking_slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_number TEXT NOT NULL UNIQUE COLLATE NOCASE,
    slot_type TEXT NOT NULL DEFAULT 'Standard' CHECK(slot_type IN ('Standard', 'Compact', 'Two-Wheeler', 'EV', 'Disabled', 'Heavy')),
    status TEXT NOT NULL DEFAULT 'Available' CHECK(status IN ('Available', 'Occupied', 'Maintenance', 'Reserved')),
    created_at TEXT NOT NULL DEFAULT (DATETIME('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_slots_status ON parking_slots(status);
CREATE INDEX IF NOT EXISTS idx_slots_type ON parking_slots(slot_type);

-- 2. Parking Sessions Table
CREATE TABLE IF NOT EXISTS parking_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_registration_number TEXT NOT NULL COLLATE NOCASE,
    vehicle_type TEXT NOT NULL DEFAULT 'Car',
    owner_name TEXT,
    owner_contact TEXT,
    slot_id INTEGER NOT NULL,
    entry_time TEXT NOT NULL DEFAULT (DATETIME('now', 'localtime')),
    exit_time TEXT,
    fee REAL DEFAULT 0.0 CHECK(fee >= 0.0),
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE', 'COMPLETED', 'CANCELLED')),
    created_at TEXT NOT NULL DEFAULT (DATETIME('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (DATETIME('now', 'localtime')),
    FOREIGN KEY (slot_id) REFERENCES parking_slots(id) ON UPDATE CASCADE ON DELETE RESTRICT
);

-- Constraint: Prevent duplicate active parking sessions for the same vehicle
CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_active_vehicle
ON parking_sessions (vehicle_registration_number)
WHERE status = 'ACTIVE';

-- Constraint: Prevent a parking slot from having multiple active occupants
CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_active_slot
ON parking_sessions (slot_id)
WHERE status = 'ACTIVE';

-- Performance indexes for lookup and history reporting
CREATE INDEX IF NOT EXISTS idx_sessions_vehicle ON parking_sessions(vehicle_registration_number);
CREATE INDEX IF NOT EXISTS idx_sessions_slot ON parking_sessions(slot_id);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON parking_sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_entry_time ON parking_sessions(entry_time);

-- 3. Application Settings Table
CREATE TABLE IF NOT EXISTS application_settings (
    setting_key TEXT PRIMARY KEY NOT NULL,
    setting_value TEXT NOT NULL,
    description TEXT,
    updated_at TEXT NOT NULL DEFAULT (DATETIME('now', 'localtime'))
);

-- 4. Defects Table (TQM Integration for Quality Control)
CREATE TABLE IF NOT EXISTS defects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date_logged TEXT NOT NULL DEFAULT (DATETIME('now', 'localtime')),
    category TEXT NOT NULL,
    description TEXT NOT NULL,
    severity INTEGER NOT NULL CHECK(severity BETWEEN 1 AND 5),
    created_at TEXT NOT NULL DEFAULT (DATETIME('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_defects_category ON defects(category);
CREATE INDEX IF NOT EXISTS idx_defects_date ON defects(date_logged);

-- 5. Backward-Compatible View: parking_records
CREATE VIEW IF NOT EXISTS parking_records AS
SELECT 
    ps.id AS id,
    ps.vehicle_registration_number AS vehicle_number,
    ps.vehicle_type AS vehicle_type,
    ps.owner_name AS owner_name,
    ps.owner_contact AS owner_contact,
    ps.slot_id AS slot_id,
    s.slot_number AS slot_number,
    ps.entry_time AS entry_time,
    ps.exit_time AS exit_time,
    ps.status AS status,
    ps.fee AS fee,
    ps.created_at AS created_at
FROM parking_sessions ps
LEFT JOIN parking_slots s ON ps.slot_id = s.id;
"""

# Default system settings seeded on initial database setup
DEFAULT_SETTINGS = [
    ("hourly_rate", "20.0", "Default standard parking rate per hour"),
    ("rate_two_wheeler", "10.0", "Hourly parking rate for two-wheelers"),
    ("rate_car", "20.0", "Hourly parking rate for standard cars"),
    ("rate_heavy", "40.0", "Hourly parking rate for heavy vehicles/trucks"),
    ("grace_period_mins", "15", "Grace period in minutes before hourly fee applies"),
    ("currency_symbol", "$", "Currency symbol for billing and receipts"),
    ("total_capacity", "50", "Initial total parking slots capacity"),
    ("system_name", "Parking Management System", "Application display name"),
]

# Initial slot configurations (50 slots across 4 categories)
DEFAULT_SLOT_CONFIGS = [
    # 20 Standard Car slots
    *[(f"A-{i:02d}", "Standard") for i in range(1, 21)],
    # 15 Compact slots
    *[(f"B-{i:02d}", "Compact") for i in range(1, 16)],
    # 10 Two-Wheeler slots
    *[(f"C-{i:02d}", "Two-Wheeler") for i in range(1, 11)],
    # 5 EV Charging slots
    *[(f"E-{i:02d}", "EV") for i in range(1, 6)],
]


# ============================================================================
# Connection Management & Automatic Initialization
# ============================================================================

def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """
    Creates and returns a connection to the SQLite database.
    
    Enforces foreign key constraint checks and dictionary-like row access.
    Automatically initializes the database if tables are missing.
    
    :param db_path: Path to database file or ':memory:'. Defaults to DEFAULT_DB_PATH.
    :return: sqlite3.Connection with PRAGMA foreign_keys = ON.
    """
    path = db_path if db_path is not None else DEFAULT_DB_PATH
    
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    
    # Enforce foreign key constraints in SQLite
    conn.execute("PRAGMA foreign_keys = ON;")
    
    # Auto-initialize if tables do not exist
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='parking_sessions';")
    if cursor.fetchone() is None:
        init_db(db_path=path, connection=conn)
        
    return conn


def init_db(db_path: Optional[str] = None, connection: Optional[sqlite3.Connection] = None) -> None:
    """
    Initializes the database schema and default configuration.
    
    Safe and non-destructive:
    - Uses CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS.
    - Preserves existing rows in parking_slots, parking_sessions, application_settings, and defects.
    - Seeds default settings and slots only if the tables are empty.
    
    :param db_path: Optional path to the database file.
    :param connection: Optional existing sqlite3.Connection to reuse.
    """
    close_when_done = False
    if connection is not None:
        conn = connection
    else:
        path = db_path if db_path is not None else DEFAULT_DB_PATH
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        close_when_done = True

    try:
        with conn:
            # Execute schema DDL script
            conn.executescript(SCHEMA_SQL)
            
            # Seed default application settings if table is empty
            for key, val, desc in DEFAULT_SETTINGS:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO application_settings (setting_key, setting_value, description)
                    VALUES (?, ?, ?);
                    """,
                    (key, val, desc)
                )
            
            # Seed default parking slots if slots table is empty
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) AS cnt FROM parking_slots;")
            row = cursor.fetchone()
            if row and row["cnt"] == 0:
                conn.executemany(
                    """
                    INSERT OR IGNORE INTO parking_slots (slot_number, slot_type, status)
                    VALUES (?, ?, 'Available');
                    """,
                    DEFAULT_SLOT_CONFIGS
                )
    finally:
        if close_when_done:
            conn.close()


# ============================================================================
# Parking Slot Operations
# ============================================================================

def get_all_slots(db_path: Optional[str] = None, status_filter: Optional[str] = None) -> List[sqlite3.Row]:
    """Retrieves all parking slots, optionally filtered by status."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        if status_filter:
            cursor.execute(
                "SELECT * FROM parking_slots WHERE status = ? ORDER BY slot_number ASC;",
                (status_filter,)
            )
        else:
            cursor.execute("SELECT * FROM parking_slots ORDER BY slot_number ASC;")
        return cursor.fetchall()
    finally:
        conn.close()


def get_available_slots(db_path: Optional[str] = None, slot_type: Optional[str] = None) -> List[sqlite3.Row]:
    """Retrieves available parking slots, optionally filtered by slot type."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        if slot_type:
            cursor.execute(
                "SELECT * FROM parking_slots WHERE status = 'Available' AND slot_type = ? ORDER BY slot_number ASC;",
                (slot_type,)
            )
        else:
            cursor.execute(
                "SELECT * FROM parking_slots WHERE status = 'Available' ORDER BY slot_number ASC;"
            )
        return cursor.fetchall()
    finally:
        conn.close()


def get_slot_by_id(slot_id: int, db_path: Optional[str] = None) -> Optional[sqlite3.Row]:
    """Retrieves a single slot record by primary key."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM parking_slots WHERE id = ?;", (slot_id,))
        return cursor.fetchone()
    finally:
        conn.close()


def get_slot_by_number(slot_number: str, db_path: Optional[str] = None) -> Optional[sqlite3.Row]:
    """Retrieves a single slot record by its slot number string (case-insensitive)."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM parking_slots WHERE slot_number = ? COLLATE NOCASE;", (slot_number,))
        return cursor.fetchone()
    finally:
        conn.close()


def create_slot(slot_number: str, slot_type: str = "Standard", db_path: Optional[str] = None) -> int:
    """Creates a new parking slot."""
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO parking_slots (slot_number, slot_type, status)
                VALUES (?, ?, 'Available');
                """,
                (slot_number.strip().upper(), slot_type)
            )
            return cursor.lastrowid
    except sqlite3.IntegrityError as e:
        raise InvalidOperationError(f"Slot '{slot_number}' could not be created: {e}") from e
    finally:
        conn.close()


def update_slot_status(slot_id: int, new_status: str, db_path: Optional[str] = None) -> None:
    """Updates the operational status of a slot ('Available', 'Occupied', 'Maintenance', 'Reserved')."""
    valid_statuses = ('Available', 'Occupied', 'Maintenance', 'Reserved')
    if new_status not in valid_statuses:
        raise ValueError(f"Invalid slot status: '{new_status}'. Must be one of {valid_statuses}")
    
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE parking_slots SET status = ? WHERE id = ?;",
                (new_status, slot_id)
            )
            if cursor.rowcount == 0:
                raise SlotNotFoundError(f"Parking slot with ID {slot_id} not found.")
    finally:
        conn.close()


# ============================================================================
# Fee Calculation Engine
# ============================================================================

def calculate_fee(
    entry_time_str: str,
    exit_time_str: str,
    vehicle_type: str = "Car",
    db_path: Optional[str] = None
) -> float:
    """
    Calculates parking fee based on elapsed duration and settings rates.
    
    Rules:
    - If elapsed minutes <= grace_period_mins, fee is $0.00.
    - Otherwise, billable hours = ceil(elapsed_minutes / 60.0).
    - Applicable hourly rate is loaded from application_settings based on vehicle type.
    """
    fmt = DATETIME_FORMAT
    # Fallback parser supporting ISO format
    try:
        entry_dt = datetime.strptime(entry_time_str, fmt)
    except ValueError:
        entry_dt = datetime.fromisoformat(entry_time_str)
        
    try:
        exit_dt = datetime.strptime(exit_time_str, fmt)
    except ValueError:
        exit_dt = datetime.fromisoformat(exit_time_str)
        
    delta_seconds = (exit_dt - entry_dt).total_seconds()
    if delta_seconds < 0:
        raise ValueError("Exit time cannot precede entry time.")
        
    elapsed_minutes = delta_seconds / 60.0
    
    # Retrieve billing settings
    grace_period_str = get_setting("grace_period_mins", default="15", db_path=db_path)
    grace_period = float(grace_period_str) if grace_period_str else 15.0
    
    if elapsed_minutes <= grace_period:
        return 0.0
        
    # Determine vehicle type hourly rate
    vtype_lower = vehicle_type.lower()
    if "two" in vtype_lower or "bike" in vtype_lower or "motorcycle" in vtype_lower:
        rate_key = "rate_two_wheeler"
    elif "heavy" in vtype_lower or "truck" in vtype_lower or "bus" in vtype_lower:
        rate_key = "rate_heavy"
    else:
        rate_key = "rate_car"
        
    hourly_rate_str = get_setting(rate_key, default="20.0", db_path=db_path)
    hourly_rate = float(hourly_rate_str) if hourly_rate_str else 20.0
    
    billable_hours = max(1, math.ceil(elapsed_minutes / 60.0))
    return round(billable_hours * hourly_rate, 2)


# ============================================================================
# Parking Session Operations (Transactional)
# ============================================================================

def start_parking_session(
    vehicle_registration_number: str,
    slot_id: int,
    vehicle_type: str = "Car",
    owner_name: Optional[str] = None,
    owner_contact: Optional[str] = None,
    entry_time: Optional[str] = None,
    db_path: Optional[str] = None
) -> int:
    """
    Starts an active parking session for a vehicle in an available slot.
    
    Atomic Transaction:
    1. Validates that the vehicle has NO existing ACTIVE session.
    2. Validates that the slot exists, is ACTIVE, and is 'Available'.
    3. Inserts the active session into parking_sessions.
    4. Updates parking_slots.status to 'Occupied'.
    
    Both database partial indexes and transactional validation protect against race conditions.
    
    :return: The new parking session ID (INTEGER).
    """
    clean_vno = vehicle_registration_number.strip().upper()
    if not clean_vno:
        raise ValueError("Vehicle registration number cannot be blank.")
        
    entry_ts = entry_time if entry_time else datetime.now().strftime(DATETIME_FORMAT)
    
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            
            # Check slot existence and availability
            cursor.execute("SELECT id, slot_number, status FROM parking_slots WHERE id = ?;", (slot_id,))
            slot = cursor.fetchone()
            if not slot:
                raise SlotNotFoundError(f"Parking slot with ID {slot_id} does not exist.")
            if slot["status"] != "Available":
                raise SlotNotAvailableError(
                    f"Parking slot '{slot['slot_number']}' is currently '{slot['status']}', not Available."
                )
            
            # Check active session for this vehicle
            cursor.execute(
                "SELECT id, slot_id FROM parking_sessions WHERE vehicle_registration_number = ? AND status = 'ACTIVE';",
                (clean_vno,)
            )
            existing = cursor.fetchone()
            if existing:
                raise ActiveSessionExistsError(
                    f"Vehicle '{clean_vno}' already has an active parking session (ID: {existing['id']})."
                )
                
            # Insert parking session
            cursor.execute(
                """
                INSERT INTO parking_sessions (
                    vehicle_registration_number,
                    vehicle_type,
                    owner_name,
                    owner_contact,
                    slot_id,
                    entry_time,
                    status
                ) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE');
                """,
                (clean_vno, vehicle_type, owner_name, owner_contact, slot_id, entry_ts)
            )
            session_id = cursor.lastrowid
            
            # Update slot status to Occupied
            cursor.execute(
                "UPDATE parking_slots SET status = 'Occupied' WHERE id = ?;",
                (slot_id,)
            )
            
            return session_id
            
    except sqlite3.IntegrityError as e:
        msg = str(e).lower()
        if "idx_unique_active_vehicle" in msg:
            raise ActiveSessionExistsError(f"Duplicate active session for vehicle '{clean_vno}'.") from e
        elif "idx_unique_active_slot" in msg:
            raise SlotNotAvailableError(f"Parking slot {slot_id} already has an active occupant.") from e
        elif "foreign key" in msg:
            raise SlotNotFoundError(f"Slot ID {slot_id} is not valid.") from e
        raise DatabaseError(f"Database integrity violation: {e}") from e
    finally:
        conn.close()


def end_parking_session(
    session_id: int,
    exit_time: Optional[str] = None,
    fee: Optional[float] = None,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Ends an active parking session, marks it COMPLETED, calculates/records the fee,
    and frees the associated parking slot back to 'Available'.
    
    Atomic Transaction:
    1. Loads the active session.
    2. Calculates fee if fee is None.
    3. Updates parking_sessions (exit_time, fee, status='COMPLETED').
    4. Updates parking_slots (status='Available').
    
    Completed session history is fully preserved.
    
    :return: Dictionary containing updated session details.
    """
    exit_ts = exit_time if exit_time else datetime.now().strftime(DATETIME_FORMAT)
    
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            
            cursor.execute(
                """
                SELECT ps.*, s.slot_number 
                FROM parking_sessions ps
                JOIN parking_slots s ON ps.slot_id = s.id
                WHERE ps.id = ?;
                """,
                (session_id,)
            )
            session = cursor.fetchone()
            if not session:
                raise SessionNotFoundError(f"Parking session with ID {session_id} not found.")
                
            if session["status"] != "ACTIVE":
                raise InvalidOperationError(
                    f"Cannot end session {session_id}: current status is '{session['status']}', not ACTIVE."
                )
                
            # Calculate fee if not explicitly passed
            calculated_fee = fee
            if calculated_fee is None:
                calculated_fee = calculate_fee(
                    entry_time_str=session["entry_time"],
                    exit_time_str=exit_ts,
                    vehicle_type=session["vehicle_type"],
                    db_path=db_path
                )
            elif calculated_fee < 0:
                raise ValueError("Parking fee cannot be negative.")
                
            now_ts = datetime.now().strftime(DATETIME_FORMAT)
            
            # Update session to COMPLETED
            cursor.execute(
                """
                UPDATE parking_sessions
                SET exit_time = ?,
                    fee = ?,
                    status = 'COMPLETED',
                    updated_at = ?
                WHERE id = ?;
                """,
                (exit_ts, calculated_fee, now_ts, session_id)
            )
            
            # Free parking slot to Available
            cursor.execute(
                "UPDATE parking_slots SET status = 'Available' WHERE id = ?;",
                (session["slot_id"],)
            )
            
            return {
                "session_id": session_id,
                "vehicle_registration_number": session["vehicle_registration_number"],
                "slot_id": session["slot_id"],
                "slot_number": session["slot_number"],
                "entry_time": session["entry_time"],
                "exit_time": exit_ts,
                "fee": calculated_fee,
                "status": "COMPLETED"
            }
    finally:
        conn.close()


def cancel_parking_session(
    session_id: int,
    db_path: Optional[str] = None
) -> None:
    """
    Cancels an active parking session (e.g. entry logged in error) and frees the slot.
    """
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, slot_id, status FROM parking_sessions WHERE id = ?;", (session_id,))
            session = cursor.fetchone()
            if not session:
                raise SessionNotFoundError(f"Parking session with ID {session_id} not found.")
            if session["status"] != "ACTIVE":
                raise InvalidOperationError(f"Session {session_id} is '{session['status']}' and cannot be cancelled.")
                
            now_ts = datetime.now().strftime(DATETIME_FORMAT)
            cursor.execute(
                """
                UPDATE parking_sessions
                SET status = 'CANCELLED', updated_at = ?
                WHERE id = ?;
                """,
                (now_ts, session_id)
            )
            cursor.execute(
                "UPDATE parking_slots SET status = 'Available' WHERE id = ?;",
                (session["slot_id"],)
            )
    finally:
        conn.close()


# ============================================================================
# Session Query Functions
# ============================================================================

def get_session_by_id(session_id: int, db_path: Optional[str] = None) -> Optional[sqlite3.Row]:
    """Retrieves session details by primary key with joined slot info."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT ps.*, s.slot_number, s.slot_type
            FROM parking_sessions ps
            LEFT JOIN parking_slots s ON ps.slot_id = s.id
            WHERE ps.id = ?;
            """,
            (session_id,)
        )
        return cursor.fetchone()
    finally:
        conn.close()


def get_active_session_by_vehicle(vehicle_number: str, db_path: Optional[str] = None) -> Optional[sqlite3.Row]:
    """Retrieves current active session for a vehicle registration number."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT ps.*, s.slot_number, s.slot_type
            FROM parking_sessions ps
            LEFT JOIN parking_slots s ON ps.slot_id = s.id
            WHERE ps.vehicle_registration_number = ? AND ps.status = 'ACTIVE';
            """,
            (vehicle_number.strip().upper(),)
        )
        return cursor.fetchone()
    finally:
        conn.close()


def get_all_active_sessions(db_path: Optional[str] = None) -> List[sqlite3.Row]:
    """Retrieves all currently active parking sessions."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT ps.*, s.slot_number, s.slot_type
            FROM parking_sessions ps
            LEFT JOIN parking_slots s ON ps.slot_id = s.id
            WHERE ps.status = 'ACTIVE'
            ORDER BY ps.entry_time DESC;
            """
        )
        return cursor.fetchall()
    finally:
        conn.close()


def get_completed_sessions(db_path: Optional[str] = None, limit: int = 100) -> List[sqlite3.Row]:
    """Retrieves completed historical parking sessions."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT ps.*, s.slot_number, s.slot_type
            FROM parking_sessions ps
            LEFT JOIN parking_slots s ON ps.slot_id = s.id
            WHERE ps.status = 'COMPLETED'
            ORDER BY ps.exit_time DESC
            LIMIT ?;
            """,
            (limit,)
        )
        return cursor.fetchall()
    finally:
        conn.close()


def get_session_history(
    db_path: Optional[str] = None,
    vehicle_number: Optional[str] = None,
    limit: int = 100
) -> List[sqlite3.Row]:
    """
    Retrieves complete parking session history, preserving past records.
    Optionally filters by vehicle registration number.
    """
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        if vehicle_number:
            cursor.execute(
                """
                SELECT ps.*, s.slot_number, s.slot_type
                FROM parking_sessions ps
                LEFT JOIN parking_slots s ON ps.slot_id = s.id
                WHERE ps.vehicle_registration_number = ? COLLATE NOCASE
                ORDER BY ps.entry_time DESC
                LIMIT ?;
                """,
                (vehicle_number.strip(), limit)
            )
        else:
            cursor.execute(
                """
                SELECT ps.*, s.slot_number, s.slot_type
                FROM parking_sessions ps
                LEFT JOIN parking_slots s ON ps.slot_id = s.id
                ORDER BY ps.entry_time DESC
                LIMIT ?;
                """,
                (limit,)
            )
        return cursor.fetchall()
    finally:
        conn.close()


def update_session_details(
    session_id: int,
    vehicle_type: Optional[str] = None,
    owner_name: Optional[str] = None,
    owner_contact: Optional[str] = None,
    db_path: Optional[str] = None
) -> None:
    """
    Updates permitted vehicle/owner details for an existing parking session.
    """
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            updates = []
            params = []
            if vehicle_type is not None:
                updates.append("vehicle_type = ?")
                params.append(vehicle_type)
            if owner_name is not None:
                updates.append("owner_name = ?")
                params.append(owner_name)
            if owner_contact is not None:
                updates.append("owner_contact = ?")
                params.append(owner_contact)

            if not updates:
                return

            now_ts = datetime.now().strftime(DATETIME_FORMAT)
            updates.append("updated_at = ?")
            params.append(now_ts)
            params.append(session_id)

            sql = f"UPDATE parking_sessions SET {', '.join(updates)} WHERE id = ?;"
            cursor.execute(sql, params)
            if cursor.rowcount == 0:
                raise SessionNotFoundError(f"Parking session with ID {session_id} not found.")
    finally:
        conn.close()


def delete_session_record(session_id: int, db_path: Optional[str] = None) -> None:
    """
    Deletes a session record from the database by ID.
    Low-level data access: callers must enforce business safety rules prior to calling.
    """
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM parking_sessions WHERE id = ?;", (session_id,))
            if cursor.rowcount == 0:
                raise SessionNotFoundError(f"Parking session with ID {session_id} not found.")
    finally:
        conn.close()


def search_sessions(
    keyword: Optional[str] = None,
    status: Optional[str] = None,
    vehicle_type: Optional[str] = None,
    slot_number: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 100,
    db_path: Optional[str] = None
) -> List[sqlite3.Row]:
    """
    Searches parking sessions with optional keyword, status, vehicle type, slot number, and date range filters.
    All filters are parameterized to prevent SQL injection.
    """
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        conditions = []
        params = []

        if keyword and keyword.strip():
            term = f"%{keyword.strip()}%"
            conditions.append(
                "(ps.vehicle_registration_number LIKE ? OR ps.owner_name LIKE ? OR s.slot_number LIKE ?)"
            )
            params.extend([term, term, term])

        if status and status.upper().strip() != "ALL":
            conditions.append("ps.status = ?")
            params.append(status.upper().strip())

        if vehicle_type and vehicle_type.strip() and vehicle_type.strip().lower() != "all":
            conditions.append("ps.vehicle_type = ?")
            params.append(vehicle_type.strip())

        if slot_number and slot_number.strip():
            conditions.append("s.slot_number = ? COLLATE NOCASE")
            params.append(slot_number.strip())

        if start_date:
            conditions.append("ps.entry_time >= ?")
            params.append(start_date)

        if end_date:
            conditions.append("ps.entry_time <= ?")
            params.append(end_date)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"""
            SELECT ps.*, s.slot_number, s.slot_type
            FROM parking_sessions ps
            LEFT JOIN parking_slots s ON ps.slot_id = s.id
            {where_clause}
            ORDER BY ps.entry_time DESC
            LIMIT ?;
        """
        params.append(limit)
        cursor.execute(sql, params)
        return cursor.fetchall()
    finally:
        conn.close()


def get_capacity_metrics(db_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Computes capacity, occupancy statistics, and recorded revenue directly from the database.
    """
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                COUNT(*) AS total_slots,
                SUM(CASE WHEN status = 'Available' THEN 1 ELSE 0 END) AS available_slots,
                SUM(CASE WHEN status = 'Occupied' THEN 1 ELSE 0 END) AS occupied_slots,
                SUM(CASE WHEN status = 'Maintenance' THEN 1 ELSE 0 END) AS maintenance_slots,
                SUM(CASE WHEN status = 'Reserved' THEN 1 ELSE 0 END) AS reserved_slots
            FROM parking_slots;
            """
        )
        row = cursor.fetchone()

        cursor.execute("SELECT COUNT(*) AS active_sessions FROM parking_sessions WHERE status = 'ACTIVE';")
        active_cnt = cursor.fetchone()["active_sessions"]

        cursor.execute(
            """
            SELECT 
                COUNT(*) AS completed_sessions,
                COALESCE(SUM(fee), 0.0) AS recorded_revenue
            FROM parking_sessions 
            WHERE status = 'COMPLETED';
            """
        )
        comp_row = cursor.fetchone()

        total = row["total_slots"] or 0
        occupied = row["occupied_slots"] or 0
        available = row["available_slots"] or 0
        utilization = round((occupied / total * 100), 1) if total > 0 else 0.0

        return {
            "total_slots": total,
            "available_slots": available,
            "occupied_slots": occupied,
            "maintenance_slots": row["maintenance_slots"] or 0,
            "reserved_slots": row["reserved_slots"] or 0,
            "active_sessions": active_cnt,
            "completed_sessions": comp_row["completed_sessions"] or 0,
            "recorded_revenue": round(comp_row["recorded_revenue"] or 0.0, 2),
            "occupancy_rate_percent": utilization
        }
    finally:
        conn.close()


def get_slots_with_occupants(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Retrieves all slots joined with active session occupant details.
    """
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT 
                s.id,
                s.slot_number,
                s.slot_type,
                s.status,
                ps.id AS session_id,
                ps.vehicle_registration_number,
                ps.vehicle_type,
                ps.entry_time
            FROM parking_slots s
            LEFT JOIN parking_sessions ps 
                ON s.id = ps.slot_id AND ps.status = 'ACTIVE'
            ORDER BY s.slot_number ASC;
            """
        )
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


# ============================================================================
# Application Settings Operations
# ============================================================================

def get_setting(key: str, default: Optional[str] = None, db_path: Optional[str] = None) -> Optional[str]:
    """Retrieves a configuration setting value by key."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT setting_value FROM application_settings WHERE setting_key = ?;", (key,))
        row = cursor.fetchone()
        return row["setting_value"] if row else default
    finally:
        conn.close()


def set_setting(key: str, value: str, description: Optional[str] = None, db_path: Optional[str] = None) -> None:
    """Sets or updates a configuration setting value."""
    if key in ("hourly_rate", "rate_two_wheeler", "rate_car", "rate_heavy", "grace_period_mins", "total_capacity"):
        try:
            num_val = float(value)
            if num_val < 0:
                raise ValueError(f"Configuration setting '{key}' cannot be negative ({value}).")
        except (ValueError, TypeError) as e:
            if "cannot be negative" in str(e):
                raise
            raise ValueError(f"Configuration setting '{key}' requires a valid non-negative number, got '{value}'.")

    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            now_ts = datetime.now().strftime(DATETIME_FORMAT)
            cursor.execute(
                """
                INSERT INTO application_settings (setting_key, setting_value, description, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(setting_key) DO UPDATE SET
                    setting_value = excluded.setting_value,
                    description = COALESCE(excluded.description, application_settings.description),
                    updated_at = excluded.updated_at;
                """,
                (key, str(value), description, now_ts)
            )
    finally:
        conn.close()


def get_all_settings(db_path: Optional[str] = None) -> Dict[str, str]:
    """Retrieves all application settings as a dictionary."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT setting_key, setting_value FROM application_settings;")
        return {row["setting_key"]: row["setting_value"] for row in cursor.fetchall()}
    finally:
        conn.close()


# ============================================================================
# Defects Logging (TQM / SQC Integration)
# ============================================================================

def log_defect(
    category: str,
    description: str,
    severity: int,
    date_logged: Optional[str] = None,
    db_path: Optional[str] = None
) -> int:
    """
    Logs a quality defect into the defects table for TQM tracking and Pareto charts.
    
    :param category: Defect category (e.g. 'Software', 'Hardware', 'Facility').
    :param description: Description of the defect.
    :param severity: Severity level between 1 and 5.
    :return: The generated defect ID.
    """
    if not (1 <= severity <= 5):
        raise ValueError("Defect severity must be an integer between 1 and 5.")
        
    date_ts = date_logged if date_logged else datetime.now().strftime(DATETIME_FORMAT)
    
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO defects (date_logged, category, description, severity)
                VALUES (?, ?, ?, ?);
                """,
                (date_ts, category.strip(), description.strip(), severity)
            )
            return cursor.lastrowid
    finally:
        conn.close()


def get_all_defects(db_path: Optional[str] = None) -> List[sqlite3.Row]:
    """Retrieves all logged defects ordered by date_logged descending."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM defects ORDER BY date_logged DESC;")
        return cursor.fetchall()
    finally:
        conn.close()


def get_defect_counts_by_category(db_path: Optional[str] = None) -> List[sqlite3.Row]:
    """Aggregates defect counts grouped by category for Pareto chart visualization."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT category, COUNT(*) AS count
            FROM defects
            GROUP BY category
            ORDER BY count DESC;
            """
        )
        return cursor.fetchall()
    finally:
        conn.close()


# ============================================================================
# Backward Compatibility Wrappers (Legacy Interface)
# ============================================================================

def create_parking_record(
    vehicle_number: str,
    vehicle_type: str = "Car",
    slot_id: Optional[int] = None,
    db_path: Optional[str] = None
) -> int:
    """
    Legacy wrapper for start_parking_session.
    If slot_id is not provided, automatically assigns the first available slot.
    """
    if slot_id is None:
        available = get_available_slots(db_path=db_path)
        if not available:
            raise SlotNotAvailableError("No available parking slots found.")
        slot_id = available[0]["id"]
        
    return start_parking_session(
        vehicle_registration_number=vehicle_number,
        slot_id=slot_id,
        vehicle_type=vehicle_type,
        db_path=db_path
    )


def get_all_active_parking_records(db_path: Optional[str] = None) -> List[sqlite3.Row]:
    """Legacy wrapper for get_all_active_sessions."""
    return get_all_active_sessions(db_path=db_path)


def update_parking_record_exit(
    record_id: int,
    fee: Optional[float] = None,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """Legacy wrapper for end_parking_session."""
    return end_parking_session(session_id=record_id, fee=fee, db_path=db_path)


def delete_parking_record(record_id: int, db_path: Optional[str] = None) -> None:
    """Legacy wrapper for cancel_parking_session."""
    cancel_parking_session(session_id=record_id, db_path=db_path)


# ============================================================================
# Main Entry Point (Command-Line Verification)
# ============================================================================

if __name__ == "__main__":
    init_db()
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) AS total_slots FROM parking_slots;")
    slots_cnt = c.fetchone()["total_slots"]
    c.execute("SELECT COUNT(*) AS total_settings FROM application_settings;")
    settings_cnt = c.fetchone()["total_settings"]
    conn.close()
    print("Database initialization successful.")
    print(f"Parking Slots seeded: {slots_cnt}")
    print(f"Application Settings seeded: {settings_cnt}")
