"""
Database Layer for Parking Management System
============================================

This module provides the persistent SQLite data access layer for the
Parking Management System, integrating operational parking session management,
multi-floor slot tracking, role-based access control (RBAC), QR ticketing,
valet management, application settings, and TQM defect logging.

Key Features & Guarantees:
- ACID Transactions for multi-step updates (e.g. entry/exit slot state sync).
- Parameterized SQL queries to prevent SQL injection.
- Database constraints & partial unique indexes enforcing:
    1. Maximum 1 active parking session per vehicle.
    2. Maximum 1 active occupant per parking slot.
- Non-destructive initialization & automated safe migrations: preserves existing data.
- Automated schema initialization on first launch or connection.
- Complete audit trail: completed sessions and activity logs remain preserved permanently.
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


class UserNotFoundError(DatabaseError):
    """Raised when a referenced user record does not exist."""
    pass


class ValetAssignmentNotFoundError(DatabaseError):
    """Raised when a referenced valet assignment record does not exist."""
    pass


class FloorNotFoundError(DatabaseError):
    """Raised when a referenced floor record does not exist."""
    pass


# ============================================================================
# Schema Definition
# ============================================================================

SCHEMA_SQL = """
-- 1. Floors Table (Multi-floor facility management)
CREATE TABLE IF NOT EXISTS floors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    floor_identifier TEXT NOT NULL UNIQUE COLLATE NOCASE,
    floor_name TEXT NOT NULL,
    total_capacity INTEGER NOT NULL DEFAULT 25 CHECK(total_capacity >= 0),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (DATETIME('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (DATETIME('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_floors_identifier ON floors(floor_identifier);
CREATE INDEX IF NOT EXISTS idx_floors_active ON floors(is_active);

-- 2. Parking Slots Table
CREATE TABLE IF NOT EXISTS parking_slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_number TEXT NOT NULL UNIQUE COLLATE NOCASE,
    slot_type TEXT NOT NULL DEFAULT 'Standard' CHECK(slot_type IN ('Standard', 'Compact', 'Two-Wheeler', 'EV', 'Disabled', 'Heavy')),
    status TEXT NOT NULL DEFAULT 'Available' CHECK(status IN ('Available', 'Occupied', 'Maintenance', 'Reserved')),
    floor_id INTEGER REFERENCES floors(id) ON UPDATE CASCADE ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (DATETIME('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_slots_status ON parking_slots(status);
CREATE INDEX IF NOT EXISTS idx_slots_type ON parking_slots(slot_type);

-- 3. Parking Sessions Table (with QR token and payment metadata)
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
    qr_token TEXT UNIQUE,
    payment_status TEXT NOT NULL DEFAULT 'PENDING' CHECK(payment_status IN ('PENDING', 'PAID', 'EXEMPT', 'REFUNDED')),
    payment_method TEXT,
    payment_time TEXT,
    processed_by_user_id INTEGER,
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

-- 4. Application Settings Table
CREATE TABLE IF NOT EXISTS application_settings (
    setting_key TEXT PRIMARY KEY NOT NULL,
    setting_value TEXT NOT NULL,
    description TEXT,
    updated_at TEXT NOT NULL DEFAULT (DATETIME('now', 'localtime'))
);

-- 5. Defects Table (TQM Integration for Quality Control)
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

-- 6. Roles Table (RBAC)
CREATE TABLE IF NOT EXISTS roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    description TEXT,
    created_at TEXT NOT NULL DEFAULT (DATETIME('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_roles_name ON roles(name);

-- 7. Permissions Table (RBAC)
CREATE TABLE IF NOT EXISTS permissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    description TEXT
);

CREATE INDEX IF NOT EXISTS idx_permissions_code ON permissions(code);

-- 8. Role Permissions Junction Table
CREATE TABLE IF NOT EXISTS role_permissions (
    role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    permission_id INTEGER NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
    PRIMARY KEY (role_id, permission_id)
);

-- 9. Users Table (Authentication and RBAC)
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    full_name TEXT NOT NULL,
    email TEXT,
    role_id INTEGER NOT NULL REFERENCES roles(id),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    last_login TEXT,
    created_at TEXT NOT NULL DEFAULT (DATETIME('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (DATETIME('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role_id);
CREATE INDEX IF NOT EXISTS idx_users_active ON users(is_active);

-- 10. Activity Logs Table (System & Staff Auditing)
CREATE TABLE IF NOT EXISTS activity_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    username TEXT,
    action TEXT NOT NULL,
    entity_type TEXT,
    entity_id TEXT,
    details TEXT,
    timestamp TEXT NOT NULL DEFAULT (DATETIME('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_activity_user ON activity_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_activity_action ON activity_logs(action);
CREATE INDEX IF NOT EXISTS idx_activity_timestamp ON activity_logs(timestamp);

-- 11. Valet Assignments Table (Lifecycle Tracking)
CREATE TABLE IF NOT EXISTS valet_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES parking_sessions(id) ON DELETE RESTRICT,
    valet_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    assigned_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'ASSIGNED' CHECK(status IN (
        'ASSIGNED',
        'VEHICLE_RECEIVED',
        'PARKING_IN_PROGRESS',
        'PARKED',
        'RETRIEVAL_REQUESTED',
        'VEHICLE_RETRIEVED',
        'DELIVERED'
    )),
    assigned_at TEXT NOT NULL DEFAULT (DATETIME('now', 'localtime')),
    received_at TEXT,
    parked_at TEXT,
    retrieval_requested_at TEXT,
    retrieved_at TEXT,
    delivered_at TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (DATETIME('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (DATETIME('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_valet_session ON valet_assignments(session_id);
CREATE INDEX IF NOT EXISTS idx_valet_user ON valet_assignments(valet_user_id);
CREATE INDEX IF NOT EXISTS idx_valet_status ON valet_assignments(status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_open_valet_session
ON valet_assignments(session_id)
WHERE status <> 'DELIVERED';

-- 12. Valet Activity Logs Table
CREATE TABLE IF NOT EXISTS valet_activity_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id INTEGER NOT NULL REFERENCES valet_assignments(id) ON DELETE CASCADE,
    valet_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    status_from TEXT,
    status_to TEXT NOT NULL,
    notes TEXT,
    timestamp TEXT NOT NULL DEFAULT (DATETIME('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_valet_log_assignment ON valet_activity_logs(assignment_id);

-- 13. Security Flags Table
CREATE TABLE IF NOT EXISTS security_flags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_registration_number TEXT NOT NULL COLLATE NOCASE,
    flagged_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    reason TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'MEDIUM' CHECK(severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE', 'RESOLVED')),
    resolution_notes TEXT,
    created_at TEXT NOT NULL DEFAULT (DATETIME('now', 'localtime')),
    resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_security_flags_plate ON security_flags(vehicle_registration_number);
CREATE INDEX IF NOT EXISTS idx_security_flags_status ON security_flags(status);

-- 14. Backward-Compatible View: parking_records
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
    ("valet_fee", "15.0", "Flat service surcharge for valet parking"),
]

# Default Floors
DEFAULT_FLOOR_CONFIGS = [
    ("G", "Ground Floor - Main & EV", 25),
    ("L1", "Level 1 - Compact & Standard", 25),
    ("B1", "Basement 1 - Two-Wheelers & Overflow", 20),
]

# Initial slot configurations (50 slots across 4 categories)
DEFAULT_SLOT_CONFIGS = [
    # 20 Standard Car slots on Ground & Level 1
    *[(f"A-{i:02d}", "Standard") for i in range(1, 21)],
    # 15 Compact slots on Level 1
    *[(f"B-{i:02d}", "Compact") for i in range(1, 16)],
    # 10 Two-Wheeler slots in Basement
    *[(f"C-{i:02d}", "Two-Wheeler") for i in range(1, 11)],
    # 5 EV Charging slots on Ground Floor
    *[(f"E-{i:02d}", "EV") for i in range(1, 6)],
]

# Default Roles
DEFAULT_ROLES = [
    ("ADMIN", "System Administrator with unrestricted access"),
    ("MANAGER", "Operations Manager supervising parking, staff, and reports"),
    ("CASHIER", "Cashier handling scanning, payments, billing, and receipts"),
    ("SECURITY", "Security Officer managing gate vehicle entry, exit, and safety"),
    ("VALET", "Valet Attendant managing vehicle parking and customer retrievals"),
]

# Granular System Permissions
DEFAULT_PERMISSIONS = [
    # User Management
    ("users:manage", "Manage Users", "Users", "Create, edit, and deactivate user accounts"),
    ("users:reset_password", "Reset Passwords", "Users", "Reset passwords for staff accounts"),
    # Floors & Slots
    ("floors:manage", "Manage Floors", "Floors", "Add, edit, and configure parking floors"),
    ("floors:view", "View Floors", "Floors", "View floor occupancy and layouts"),
    ("slots:manage", "Manage Slots", "Slots", "Create, configure, and maintain parking slots"),
    ("slots:view", "View Slots", "Slots", "View slot availability and occupancy"),
    # Vehicles & Sessions
    ("vehicles:manage", "Manage Vehicle Records", "Vehicles", "Edit vehicle records and historical sessions"),
    ("vehicles:register", "Register Vehicle Entry", "Vehicles", "Register incoming vehicles and generate tickets"),
    ("vehicles:entry", "Record Entry", "Vehicles", "Record gate vehicle entry"),
    ("vehicles:exit", "Verify & Record Exit", "Vehicles", "Verify payment and record vehicle exit"),
    ("vehicles:search", "Search Vehicles", "Vehicles", "Search active and historical vehicle sessions"),
    ("vehicles:flag", "Flag Vehicles", "Vehicles", "Flag suspicious vehicles for security review"),
    ("vehicles:status", "View Vehicle Status", "Vehicles", "Check vehicle location and session state"),
    ("vehicles:monitor", "Monitor Vehicles", "Vehicles", "Live operational monitoring of vehicles"),
    # Rates & Billing
    ("rates:manage", "Manage Rates", "Billing", "Update hourly rates and fee policies"),
    ("billing:calculate", "Calculate Charges", "Billing", "Compute parking fees and applicable discounts"),
    ("billing:process", "Process Payments", "Billing", "Accept payment transactions via cash/card/UPI"),
    ("billing:view_all", "View All Transactions", "Billing", "Inspect full transaction history across staff"),
    ("refunds:approve", "Approve Refunds", "Billing", "Authorize parking fee refunds and adjustments"),
    ("receipts:generate", "Generate Receipts", "Receipts", "Generate detailed electronic receipts"),
    ("receipts:print", "Print Receipts", "Receipts", "Print parking tickets and payment receipts"),
    ("transactions:personal", "Personal Transactions", "Billing", "View own shift transactions"),
    # QR Code
    ("qr:scan", "Scan QR Codes", "QR", "Scan vehicle QR tokens for verification and billing"),
    # Valet
    ("valet:manage", "Manage Valet", "Valet", "Assign valets and oversee parking workflow"),
    ("valet:monitor", "Monitor Valet Board", "Valet", "Monitor valet operations in real time"),
    ("valet:view_assigned", "View Assigned Tasks", "Valet", "View assigned vehicle parking and retrievals"),
    ("valet:accept_handover", "Accept Handover", "Valet", "Accept vehicle keys from security/driver"),
    ("valet:update_progress", "Update Progress", "Valet", "Update vehicle parking progress"),
    ("valet:retrieval", "Retrieve Vehicle", "Valet", "Retrieve parked vehicle upon request"),
    ("valet:deliver", "Deliver Vehicle", "Valet", "Deliver retrieved vehicle to customer"),
    ("valet:personal_history", "Valet History", "Valet", "View personal assignment history"),
    # Reports & Auditing
    ("reports:financial", "Financial Reports", "Reports", "Access full revenue, payment, and audit reports"),
    ("reports:operational", "Operational Reports", "Reports", "Access occupancy, turnover, and slot reports"),
    ("reports:revenue", "Revenue Overview", "Reports", "View revenue summaries"),
    ("activity:view_logs", "View Activity Logs", "Activity", "Inspect staff actions and security audit logs"),
    ("staff:monitor", "Monitor Staff", "Staff", "Monitor active staff shifts and throughput"),
    ("staff:performance", "Staff Performance", "Staff", "Evaluate cashier and valet performance metrics"),
    ("settings:manage", "System Settings", "Settings", "Configure developer and system parameters"),
    ("exceptions:handle", "Handle Exceptions", "Operations", "Override slot assignments and exceptions"),
    # Dashboards
    ("dashboard:admin", "Admin Dashboard", "Dashboards", "Access executive admin dashboard"),
    ("dashboard:manager", "Manager Dashboard", "Dashboards", "Access operations manager dashboard"),
    ("dashboard:cashier", "Cashier Dashboard", "Dashboards", "Access cashier billing station"),
    ("dashboard:security", "Security Dashboard", "Dashboards", "Access security gate station"),
    ("dashboard:valet", "Valet Dashboard", "Dashboards", "Access valet attendant board"),
]

# Role-to-Permissions Mapping
ROLE_PERMISSION_MAPPINGS = {
    "ADMIN": [p[0] for p in DEFAULT_PERMISSIONS],  # Full system access
    "MANAGER": [
        "dashboard:manager",
        "floors:view",
        "slots:manage",
        "slots:view",
        "vehicles:monitor",
        "vehicles:search",
        "vehicles:status",
        "staff:monitor",
        "valet:monitor",
        "valet:manage",
        "reports:revenue",
        "reports:operational",
        "exceptions:handle",
        "refunds:approve",
        "activity:view_logs",
    ],
    "CASHIER": [
        "dashboard:cashier",
        "vehicles:search",
        "qr:scan",
        "billing:calculate",
        "billing:process",
        "receipts:generate",
        "receipts:print",
        "transactions:personal",
        "slots:view",
    ],
    "SECURITY": [
        "dashboard:security",
        "vehicles:register",
        "vehicles:entry",
        "vehicles:exit",
        "qr:scan",
        "slots:view",
        "vehicles:flag",
        "vehicles:status",
        "receipts:print",
    ],
    "VALET": [
        "dashboard:valet",
        "valet:view_assigned",
        "valet:accept_handover",
        "valet:update_progress",
        "valet:retrieval",
        "valet:deliver",
        "valet:personal_history",
        "slots:view",
    ],
}

# Seeded local demo users. Plaintext passwords are kept outside the repository
# in demo_credentials.local.txt and are only exposed when demo mode is enabled.
DEFAULT_USERS_SEEDS = [
    ("admin", "$2b$12$pnOb4pazuUsVzh.pRcy4Su3L6LuiHUXQsan1vXRQtS87LJjyFNhC.", "System Administrator", "admin@parkingsystem.local", "ADMIN"),
    ("manager", "$2b$12$zIcbZbHceLpPgQGhQS8EIei2ogZGBdpu3Qln7JBwaCAhDRt8ag3eS", "Operational Manager", "manager@parkingsystem.local", "MANAGER"),
    ("cashier", "$2b$12$L2dnkdrRJlHT.FdcBKx8w.fWH937vE90.1MGXcT6QkaCpxW2XZ5La", "Primary Cashier", "cashier@parkingsystem.local", "CASHIER"),
    ("security", "$2b$12$HBUUR.3BKwSV7sPO.EytbuJYKjyX6qzCGYh/6ACPObBeWFNz0r3vO", "Gate Security Officer", "security@parkingsystem.local", "SECURITY"),
    ("valet", "$2b$12$94QlsEy5yja9akyeBx7nOucInNXmIL8u4/KEGeRkgJrEGnTpxxCRO", "Valet Attendant", "valet@parkingsystem.local", "VALET"),
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
    
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    
    # Enforce foreign key constraints in SQLite
    conn.execute("PRAGMA foreign_keys = ON;")
    
    # Auto-initialize if tables do not exist
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='parking_sessions';")
    if cursor.fetchone() is None:
        init_db(db_path=path, connection=conn)
        
    return conn


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Safe, non-destructive migrations to add any missing columns/tables."""
    cursor = conn.cursor()

    # Librarian was a legacy alias, not a supported role. Remove only that
    # obsolete account; parking/session records and audit rows remain intact.
    cursor.execute("DELETE FROM users WHERE username = 'librarian' COLLATE NOCASE;")

    # 1. Check parking_slots columns
    cursor.execute("PRAGMA table_info(parking_slots);")
    slot_cols = [row["name"] for row in cursor.fetchall()]
    if "floor_id" not in slot_cols:
        cursor.execute("ALTER TABLE parking_slots ADD COLUMN floor_id INTEGER REFERENCES floors(id);")

    # 2. Check parking_sessions columns
    cursor.execute("PRAGMA table_info(parking_sessions);")
    session_cols = [row["name"] for row in cursor.fetchall()]
    if "qr_token" not in session_cols:
        cursor.execute("ALTER TABLE parking_sessions ADD COLUMN qr_token TEXT;")
    if "payment_status" not in session_cols:
        cursor.execute("ALTER TABLE parking_sessions ADD COLUMN payment_status TEXT NOT NULL DEFAULT 'PENDING';")
    if "payment_method" not in session_cols:
        cursor.execute("ALTER TABLE parking_sessions ADD COLUMN payment_method TEXT;")
    if "payment_time" not in session_cols:
        cursor.execute("ALTER TABLE parking_sessions ADD COLUMN payment_time TEXT;")
    if "processed_by_user_id" not in session_cols:
        cursor.execute("ALTER TABLE parking_sessions ADD COLUMN processed_by_user_id INTEGER;")

    # These indexes are created only after the migration checks above.  Creating
    # them from the base schema first breaks initialization of older databases
    # that do not yet have the migrated columns.
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_slots_floor ON parking_slots(floor_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_qr_token ON parking_sessions(qr_token);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_payment_status ON parking_sessions(payment_status);")
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_open_valet_session
        ON valet_assignments(session_id)
        WHERE status <> 'DELIVERED';
        """
    )

    # 3. Seed floors if empty
    cursor.execute("SELECT COUNT(*) AS cnt FROM floors;")
    if cursor.fetchone()["cnt"] == 0:
        cursor.executemany(
            """
            INSERT OR IGNORE INTO floors (floor_identifier, floor_name, total_capacity)
            VALUES (?, ?, ?);
            """,
            DEFAULT_FLOOR_CONFIGS
        )

    # 4. Backfill floor_id on unassigned slots
    cursor.execute("SELECT id, floor_identifier FROM floors;")
    floor_map = {row["floor_identifier"].upper(): row["id"] for row in cursor.fetchall()}
    g_id = floor_map.get("G")
    l1_id = floor_map.get("L1")
    b1_id = floor_map.get("B1")

    if g_id:
        cursor.execute(
            "UPDATE parking_slots SET floor_id = ? WHERE floor_id IS NULL AND (slot_number LIKE 'A-%' OR slot_number LIKE 'E-%' OR slot_type = 'EV');",
            (g_id,)
        )
    if l1_id:
        cursor.execute(
            "UPDATE parking_slots SET floor_id = ? WHERE floor_id IS NULL AND (slot_number LIKE 'B-%' OR slot_type IN ('Compact', 'Standard'));",
            (l1_id,)
        )
    if b1_id:
        cursor.execute(
            "UPDATE parking_slots SET floor_id = ? WHERE floor_id IS NULL AND (slot_number LIKE 'C-%' OR slot_type = 'Two-Wheeler');",
            (b1_id,)
        )
    # Default any remaining unassigned slots to Ground Floor
    if g_id:
        cursor.execute("UPDATE parking_slots SET floor_id = ? WHERE floor_id IS NULL;", (g_id,))

    # 5. Seed Roles if empty
    cursor.execute("SELECT COUNT(*) AS cnt FROM roles;")
    if cursor.fetchone()["cnt"] == 0:
        cursor.executemany(
            "INSERT OR IGNORE INTO roles (name, description) VALUES (?, ?);",
            DEFAULT_ROLES
        )

    # 6. Seed Permissions if empty
    cursor.execute("SELECT COUNT(*) AS cnt FROM permissions;")
    if cursor.fetchone()["cnt"] == 0:
        cursor.executemany(
            "INSERT OR IGNORE INTO permissions (code, name, category, description) VALUES (?, ?, ?, ?);",
            DEFAULT_PERMISSIONS
        )

    # 7. Seed Role Permissions if empty
    cursor.execute("SELECT COUNT(*) AS cnt FROM role_permissions;")
    if cursor.fetchone()["cnt"] == 0:
        cursor.execute("SELECT id, name FROM roles;")
        roles_dict = {row["name"].upper(): row["id"] for row in cursor.fetchall()}

        cursor.execute("SELECT id, code FROM permissions;")
        perms_dict = {row["code"]: row["id"] for row in cursor.fetchall()}

        for role_name, perm_codes in ROLE_PERMISSION_MAPPINGS.items():
            role_id = roles_dict.get(role_name.upper())
            if not role_id:
                continue
            for code in perm_codes:
                perm_id = perms_dict.get(code)
                if perm_id:
                    cursor.execute(
                        "INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (?, ?);",
                        (role_id, perm_id)
                    )

    # 8. Ensure each supported demo account exists. Existing passwords and
    # profile data are preserved, while the supported role assignment is kept
    # authoritative for these named local accounts.
    cursor.execute("SELECT id, name FROM roles;")
    roles_dict = {row["name"].upper(): row["id"] for row in cursor.fetchall()}
    for username, pwd_hash, full_name, email, role_name in DEFAULT_USERS_SEEDS:
        role_id = roles_dict.get(role_name.upper(), 1)
        cursor.execute(
            """
            INSERT OR IGNORE INTO users (username, password_hash, full_name, email, role_id, is_active)
            VALUES (?, ?, ?, ?, ?, 1);
            """,
            (username, pwd_hash, full_name, email, role_id)
        )
        cursor.execute(
            "UPDATE users SET role_id = ? WHERE username = ? COLLATE NOCASE;",
            (role_id, username)
        )


def init_db(db_path: Optional[str] = None, connection: Optional[sqlite3.Connection] = None) -> None:
    """
    Initializes the database schema, safe migrations, and default configuration.
    
    Safe and non-destructive:
    - Uses CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS.
    - Preserves existing rows in parking_slots, parking_sessions, application_settings, defects, and users.
    - Seeds default settings, floors, slots, roles, permissions, and users only if empty.
    
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
            # Execute base schema DDL script
            conn.executescript(SCHEMA_SQL)
            
            # Run non-destructive column and data migrations
            _run_migrations(conn)

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
                _run_migrations(conn)
    finally:
        if close_when_done:
            conn.close()


# ============================================================================
# Floor Operations (Multi-Floor Management)
# ============================================================================

def create_floor(
    floor_identifier: str,
    floor_name: str,
    total_capacity: int = 25,
    db_path: Optional[str] = None
) -> int:
    """Creates a new parking floor with an identifier and capacity."""
    clean_id = floor_identifier.strip().upper()
    clean_name = floor_name.strip()
    if not clean_id or not clean_name:
        raise ValueError("Floor identifier and floor name cannot be empty.")
    if total_capacity < 0:
        raise ValueError("Floor capacity cannot be negative.")

    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO floors (floor_identifier, floor_name, total_capacity, is_active)
                VALUES (?, ?, ?, 1);
                """,
                (clean_id, clean_name, total_capacity)
            )
            return cursor.lastrowid
    except sqlite3.IntegrityError as e:
        raise InvalidOperationError(f"Floor '{clean_id}' already exists or violates constraint: {e}") from e
    finally:
        conn.close()


def update_floor(
    floor_id: int,
    floor_name: Optional[str] = None,
    total_capacity: Optional[int] = None,
    is_active: Optional[int] = None,
    db_path: Optional[str] = None
) -> None:
    """Updates floor details, capacity, or active status."""
    updates = []
    params = []
    if floor_name is not None:
        updates.append("floor_name = ?")
        params.append(floor_name.strip())
    if total_capacity is not None:
        if total_capacity < 0:
            raise ValueError("Floor capacity cannot be negative.")
        updates.append("total_capacity = ?")
        params.append(total_capacity)
    if is_active is not None:
        if is_active not in (0, 1):
            raise ValueError("is_active must be 0 or 1.")
        updates.append("is_active = ?")
        params.append(is_active)

    if not updates:
        return

    now_ts = datetime.now().strftime(DATETIME_FORMAT)
    updates.append("updated_at = ?")
    params.append(now_ts)
    params.append(floor_id)

    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(f"UPDATE floors SET {', '.join(updates)} WHERE id = ?;", params)
            if cursor.rowcount == 0:
                raise FloorNotFoundError(f"Floor with ID {floor_id} not found.")
    finally:
        conn.close()


def get_all_floors(active_only: bool = False, db_path: Optional[str] = None) -> List[sqlite3.Row]:
    """Retrieves all floors ordered by identifier."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        if active_only:
            cursor.execute("SELECT * FROM floors WHERE is_active = 1 ORDER BY floor_identifier ASC;")
        else:
            cursor.execute("SELECT * FROM floors ORDER BY floor_identifier ASC;")
        return cursor.fetchall()
    finally:
        conn.close()


def get_floor_by_id(floor_id: int, db_path: Optional[str] = None) -> Optional[sqlite3.Row]:
    """Retrieves a single floor record by primary key."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM floors WHERE id = ?;", (floor_id,))
        return cursor.fetchone()
    finally:
        conn.close()


def get_floor_by_identifier(identifier: str, db_path: Optional[str] = None) -> Optional[sqlite3.Row]:
    """Retrieves a single floor record by its identifier string (case-insensitive)."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM floors WHERE floor_identifier = ? COLLATE NOCASE;", (identifier.strip(),))
        return cursor.fetchone()
    finally:
        conn.close()


def get_floor_occupancy_summary(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Computes real-time occupancy statistics per floor:
    - Floor name & identifier
    - Total slots configured
    - Available, Occupied, Reserved, Maintenance slots
    - Occupancy percentage
    """
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT 
                f.id AS floor_id,
                f.floor_identifier,
                f.floor_name,
                f.total_capacity,
                f.is_active,
                COUNT(s.id) AS total_slots,
                SUM(CASE WHEN s.status = 'Available' THEN 1 ELSE 0 END) AS available_slots,
                SUM(CASE WHEN s.status = 'Occupied' THEN 1 ELSE 0 END) AS occupied_slots,
                SUM(CASE WHEN s.status = 'Reserved' THEN 1 ELSE 0 END) AS reserved_slots,
                SUM(CASE WHEN s.status = 'Maintenance' THEN 1 ELSE 0 END) AS maintenance_slots
            FROM floors f
            LEFT JOIN parking_slots s ON f.id = s.floor_id
            GROUP BY f.id, f.floor_identifier, f.floor_name, f.total_capacity, f.is_active
            ORDER BY f.floor_identifier ASC;
            """
        )
        results = []
        for row in cursor.fetchall():
            tot = row["total_slots"] or 0
            occ = row["occupied_slots"] or 0
            avail = row["available_slots"] or 0
            pct = round((occ / tot * 100.0), 1) if tot > 0 else 0.0
            results.append({
                "floor_id": row["floor_id"],
                "floor_identifier": row["floor_identifier"],
                "floor_name": row["floor_name"],
                "total_capacity": row["total_capacity"],
                "is_active": bool(row["is_active"]),
                "total_slots": tot,
                "available_slots": avail,
                "occupied_slots": occ,
                "reserved_slots": row["reserved_slots"] or 0,
                "maintenance_slots": row["maintenance_slots"] or 0,
                "occupancy_percentage": pct,
            })
        return results
    finally:
        conn.close()


def get_slots_by_floor(
    floor_id: int,
    status_filter: Optional[str] = None,
    db_path: Optional[str] = None
) -> List[sqlite3.Row]:
    """Retrieves all slots for a given floor with optional status filter."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        if status_filter:
            cursor.execute(
                "SELECT * FROM parking_slots WHERE floor_id = ? AND status = ? ORDER BY slot_number ASC;",
                (floor_id, status_filter)
            )
        else:
            cursor.execute(
                "SELECT * FROM parking_slots WHERE floor_id = ? ORDER BY slot_number ASC;",
                (floor_id,)
            )
        return cursor.fetchall()
    finally:
        conn.close()


# ============================================================================
# Parking Slot Operations
# ============================================================================

def get_all_slots(
    db_path: Optional[str] = None,
    status_filter: Optional[str] = None,
    floor_id: Optional[int] = None
) -> List[sqlite3.Row]:
    """Retrieves all parking slots, optionally filtered by status and floor."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        conditions = []
        params = []
        if status_filter:
            conditions.append("s.status = ?")
            params.append(status_filter)
        if floor_id is not None:
            conditions.append("s.floor_id = ?")
            params.append(floor_id)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"""
            SELECT s.*, f.floor_identifier, f.floor_name
            FROM parking_slots s
            LEFT JOIN floors f ON s.floor_id = f.id
            {where_clause}
            ORDER BY s.slot_number ASC;
        """
        cursor.execute(sql, params)
        return cursor.fetchall()
    finally:
        conn.close()


def get_available_slots(
    db_path: Optional[str] = None,
    slot_type: Optional[str] = None,
    floor_id: Optional[int] = None
) -> List[sqlite3.Row]:
    """Retrieves available parking slots, optionally filtered by slot type and floor."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        conditions = ["s.status = 'Available'"]
        params = []
        if slot_type:
            conditions.append("s.slot_type = ?")
            params.append(slot_type)
        if floor_id is not None:
            conditions.append("s.floor_id = ?")
            params.append(floor_id)

        sql = f"""
            SELECT s.*, f.floor_identifier, f.floor_name
            FROM parking_slots s
            LEFT JOIN floors f ON s.floor_id = f.id
            WHERE {' AND '.join(conditions)}
            ORDER BY s.slot_number ASC;
        """
        cursor.execute(sql, params)
        return cursor.fetchall()
    finally:
        conn.close()


def get_slot_by_id(slot_id: int, db_path: Optional[str] = None) -> Optional[sqlite3.Row]:
    """Retrieves a single slot record by primary key."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT s.*, f.floor_identifier, f.floor_name
            FROM parking_slots s
            LEFT JOIN floors f ON s.floor_id = f.id
            WHERE s.id = ?;
            """,
            (slot_id,)
        )
        return cursor.fetchone()
    finally:
        conn.close()


def get_slot_by_number(slot_number: str, db_path: Optional[str] = None) -> Optional[sqlite3.Row]:
    """Retrieves a single slot record by its slot number string (case-insensitive)."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT s.*, f.floor_identifier, f.floor_name
            FROM parking_slots s
            LEFT JOIN floors f ON s.floor_id = f.id
            WHERE s.slot_number = ? COLLATE NOCASE;
            """,
            (slot_number,)
        )
        return cursor.fetchone()
    finally:
        conn.close()


def create_slot(
    slot_number: str,
    slot_type: str = "Standard",
    floor_id: Optional[int] = None,
    db_path: Optional[str] = None
) -> int:
    """Creates a new parking slot associated optionally with a floor."""
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            
            # If floor_id is not provided, try to infer from first floor or slot name
            if floor_id is None:
                cursor.execute("SELECT id FROM floors LIMIT 1;")
                f_row = cursor.fetchone()
                floor_id = f_row["id"] if f_row else None

            cursor.execute(
                """
                INSERT INTO parking_slots (slot_number, slot_type, status, floor_id)
                VALUES (?, ?, 'Available', ?);
                """,
                (slot_number.strip().upper(), slot_type, floor_id)
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
    qr_token: Optional[str] = None,
    processed_by_user_id: Optional[int] = None,
    db_path: Optional[str] = None
) -> int:
    """
    Starts an active parking session for a vehicle in an available slot.
    
    Atomic Transaction:
    1. Validates that the vehicle has NO existing ACTIVE session.
    2. Validates that the slot exists, is ACTIVE, and is 'Available'.
    3. Inserts the active session into parking_sessions with QR token.
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
                    status,
                    qr_token,
                    payment_status,
                    processed_by_user_id
                ) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?, 'PENDING', ?);
                """,
                (clean_vno, vehicle_type, owner_name, owner_contact, slot_id, entry_ts, qr_token, processed_by_user_id)
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
    payment_status: str = "PAID",
    payment_method: Optional[str] = "Cash",
    processed_by_user_id: Optional[int] = None,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Ends an active parking session, marks it COMPLETED, calculates/records the fee,
    records payment details, and frees the associated parking slot back to 'Available'.
    
    Atomic Transaction:
    1. Loads the active session.
    2. Calculates fee if fee is None.
    3. Updates parking_sessions (exit_time, fee, status='COMPLETED', payment metadata).
    4. Updates parking_slots (status='Available').
    
    Completed session history is fully preserved.
    
    :return: Dictionary containing updated session details.
    """
    exit_ts = exit_time if exit_time else datetime.now().strftime(DATETIME_FORMAT)
    if payment_status not in ("PAID", "EXEMPT"):
        raise InvalidOperationError(
            "A completed parking session must have payment status 'PAID' or 'EXEMPT'."
        )
    if not isinstance(payment_method, str) or not payment_method.strip():
        raise InvalidOperationError("A payment method is required to complete a session.")

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
                    payment_status = ?,
                    payment_method = ?,
                    payment_time = ?,
                    processed_by_user_id = COALESCE(?, processed_by_user_id),
                    updated_at = ?
                WHERE id = ?;
                """,
                (exit_ts, calculated_fee, payment_status, payment_method, now_ts, processed_by_user_id, now_ts, session_id)
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
                "status": "COMPLETED",
                "payment_status": payment_status,
                "payment_method": payment_method
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
    """Retrieves session details by primary key with joined slot and floor info."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT ps.*, s.slot_number, s.slot_type, f.floor_identifier, f.floor_name
            FROM parking_sessions ps
            LEFT JOIN parking_slots s ON ps.slot_id = s.id
            LEFT JOIN floors f ON s.floor_id = f.id
            WHERE ps.id = ?;
            """,
            (session_id,)
        )
        return cursor.fetchone()
    finally:
        conn.close()


def get_session_by_qr_token(qr_token: str, db_path: Optional[str] = None) -> Optional[sqlite3.Row]:
    """Retrieves session details by its cryptographic QR token."""
    if not qr_token or not isinstance(qr_token, str):
        return None
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT ps.*, s.slot_number, s.slot_type, f.floor_identifier, f.floor_name
            FROM parking_sessions ps
            LEFT JOIN parking_slots s ON ps.slot_id = s.id
            LEFT JOIN floors f ON s.floor_id = f.id
            WHERE ps.qr_token = ?;
            """,
            (qr_token.strip(),)
        )
        return cursor.fetchone()
    finally:
        conn.close()


def attach_qr_token_to_session(session_id: int, qr_token: str, db_path: Optional[str] = None) -> None:
    """Attaches or updates a cryptographic QR token to an existing parking session."""
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE parking_sessions SET qr_token = ?, updated_at = DATETIME('now', 'localtime') WHERE id = ?;",
                (qr_token.strip(), session_id)
            )
            if cursor.rowcount == 0:
                raise SessionNotFoundError(f"Session with ID {session_id} not found.")
    finally:
        conn.close()


def update_session_payment(
    session_id: int,
    payment_status: str,
    payment_method: Optional[str] = None,
    fee: Optional[float] = None,
    processed_by_user_id: Optional[int] = None,
    db_path: Optional[str] = None
) -> None:
    """Updates payment status, method, and fee for a session."""
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            now_ts = datetime.now().strftime(DATETIME_FORMAT)
            updates = ["payment_status = ?", "payment_time = ?", "updated_at = ?"]
            params = [payment_status, now_ts, now_ts]
            if payment_method is not None:
                updates.append("payment_method = ?")
                params.append(payment_method)
            if fee is not None:
                updates.append("fee = ?")
                params.append(fee)
            if processed_by_user_id is not None:
                updates.append("processed_by_user_id = ?")
                params.append(processed_by_user_id)
            params.append(session_id)

            cursor.execute(f"UPDATE parking_sessions SET {', '.join(updates)} WHERE id = ?;", params)
            if cursor.rowcount == 0:
                raise SessionNotFoundError(f"Session with ID {session_id} not found.")
    finally:
        conn.close()


def get_active_session_by_vehicle(vehicle_number: str, db_path: Optional[str] = None) -> Optional[sqlite3.Row]:
    """Retrieves current active session for a vehicle registration number."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT ps.*, s.slot_number, s.slot_type, f.floor_identifier, f.floor_name
            FROM parking_sessions ps
            LEFT JOIN parking_slots s ON ps.slot_id = s.id
            LEFT JOIN floors f ON s.floor_id = f.id
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
            SELECT ps.*, s.slot_number, s.slot_type, f.floor_identifier, f.floor_name
            FROM parking_sessions ps
            LEFT JOIN parking_slots s ON ps.slot_id = s.id
            LEFT JOIN floors f ON s.floor_id = f.id
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
            SELECT ps.*, s.slot_number, s.slot_type, f.floor_identifier, f.floor_name
            FROM parking_sessions ps
            LEFT JOIN parking_slots s ON ps.slot_id = s.id
            LEFT JOIN floors f ON s.floor_id = f.id
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
                SELECT ps.*, s.slot_number, s.slot_type, f.floor_identifier, f.floor_name
                FROM parking_sessions ps
                LEFT JOIN parking_slots s ON ps.slot_id = s.id
                LEFT JOIN floors f ON s.floor_id = f.id
                WHERE ps.vehicle_registration_number = ? COLLATE NOCASE
                ORDER BY ps.entry_time DESC
                LIMIT ?;
                """,
                (vehicle_number.strip(), limit)
            )
        else:
            cursor.execute(
                """
                SELECT ps.*, s.slot_number, s.slot_type, f.floor_identifier, f.floor_name
                FROM parking_sessions ps
                LEFT JOIN parking_slots s ON ps.slot_id = s.id
                LEFT JOIN floors f ON s.floor_id = f.id
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
                "(ps.vehicle_registration_number LIKE ? OR ps.owner_name LIKE ? OR s.slot_number LIKE ? OR ps.qr_token LIKE ?)"
            )
            params.extend([term, term, term, term])

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
            SELECT ps.*, s.slot_number, s.slot_type, f.floor_identifier, f.floor_name
            FROM parking_sessions ps
            LEFT JOIN parking_slots s ON ps.slot_id = s.id
            LEFT JOIN floors f ON s.floor_id = f.id
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


def get_role_dashboard_metrics(
    role: str,
    user_id: Optional[int] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Returns live role-specific dashboard metrics from one database connection."""
    role_upper = (role or "").upper()
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                COUNT(*) AS total_sessions,
                COALESCE(SUM(status = 'ACTIVE'), 0) AS active_sessions,
                COALESCE(SUM(CASE WHEN date(entry_time) = date('now', 'localtime') THEN 1 ELSE 0 END), 0) AS entries_today,
                COALESCE(SUM(CASE WHEN status = 'COMPLETED' AND date(exit_time) = date('now', 'localtime') THEN 1 ELSE 0 END), 0) AS exits_today,
                COALESCE(SUM(CASE WHEN status = 'COMPLETED' AND date(exit_time) = date('now', 'localtime') THEN fee ELSE 0 END), 0.0) AS revenue_today,
                COALESCE(SUM(CASE WHEN status = 'COMPLETED' AND strftime('%Y-%m', exit_time) = strftime('%Y-%m', 'now', 'localtime') THEN fee ELSE 0 END), 0.0) AS revenue_month
            FROM parking_sessions;
            """
        )
        sessions = dict(cursor.fetchone())
        cursor.execute(
            """
            SELECT
                COUNT(*) AS total_slots,
                COALESCE(SUM(status = 'Available'), 0) AS available_slots,
                COALESCE(SUM(status = 'Occupied'), 0) AS occupied_slots
            FROM parking_slots;
            """
        )
        slots = dict(cursor.fetchone())
        cursor.execute("SELECT COUNT(*) AS total_floors FROM floors WHERE is_active = 1;")
        floors = dict(cursor.fetchone())
        cursor.execute(
            "SELECT COUNT(*) AS active_users FROM users WHERE is_active = 1;"
        )
        users = dict(cursor.fetchone())
        cursor.execute(
            """
            SELECT
                COUNT(*) AS active_assignments,
                COALESCE(SUM(status = 'RETRIEVAL_REQUESTED'), 0) AS retrieval_requests,
                COALESCE(SUM(status = 'DELIVERED'), 0) AS delivered
            FROM valet_assignments
            WHERE (? IS NULL OR valet_user_id = ?);
            """,
            (
                user_id if role_upper == "VALET" else None,
                user_id if role_upper == "VALET" else None,
            ),
        )
        valet = dict(cursor.fetchone())
        cursor.execute(
            "SELECT COUNT(*) AS active_valets FROM users u JOIN roles r ON r.id = u.role_id WHERE r.name = 'VALET' AND u.is_active = 1;"
        )
        active_valets = dict(cursor.fetchone())
        return {
            **sessions,
            **slots,
            **floors,
            **users,
            **valet,
            **active_valets,
            "occupancy_percent": round(
                (slots["occupied_slots"] / slots["total_slots"] * 100)
                if slots["total_slots"]
                else 0,
                1,
            ),
        }
    finally:
        conn.close()


def get_slots_with_occupants(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Retrieves all slots joined with active session occupant details and floor information.
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
                s.floor_id,
                f.floor_identifier,
                f.floor_name,
                ps.id AS session_id,
                ps.vehicle_registration_number,
                ps.vehicle_type,
                ps.entry_time
            FROM parking_slots s
            LEFT JOIN floors f ON s.floor_id = f.id
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
    if key in ("hourly_rate", "rate_two_wheeler", "rate_car", "rate_heavy", "grace_period_mins", "total_capacity", "valet_fee"):
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
# User and RBAC Operations
# ============================================================================

def get_user_by_username(username: str, db_path: Optional[str] = None) -> Optional[sqlite3.Row]:
    """Retrieves user by username with joined role information."""
    if not username:
        return None
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT u.*, r.name AS role_name
            FROM users u
            JOIN roles r ON u.role_id = r.id
            WHERE u.username = ? COLLATE NOCASE;
            """,
            (username.strip(),)
        )
        return cursor.fetchone()
    finally:
        conn.close()


def get_user_by_id(user_id: int, db_path: Optional[str] = None) -> Optional[sqlite3.Row]:
    """Retrieves user by ID with joined role information."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT u.*, r.name AS role_name
            FROM users u
            JOIN roles r ON u.role_id = r.id
            WHERE u.id = ?;
            """,
            (user_id,)
        )
        return cursor.fetchone()
    finally:
        conn.close()


def get_all_users(db_path: Optional[str] = None) -> List[sqlite3.Row]:
    """Retrieves all user records ordered by username."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT u.id, u.username, u.full_name, u.email, u.role_id, r.name AS role_name, u.is_active, u.last_login, u.created_at
            FROM users u
            JOIN roles r ON u.role_id = r.id
            ORDER BY u.username ASC;
            """
        )
        return cursor.fetchall()
    finally:
        conn.close()


def create_user_record(
    username: str,
    password_hash: str,
    full_name: str,
    role_name: str,
    email: Optional[str] = None,
    is_active: int = 1,
    db_path: Optional[str] = None
) -> int:
    """Creates a new system user record with a designated role."""
    clean_username = username.strip().lower()
    clean_name = full_name.strip()
    if not clean_username or not clean_name:
        raise ValueError("Username and full name cannot be blank.")

    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM roles WHERE name = ? COLLATE NOCASE;", (role_name.strip(),))
            role = cursor.fetchone()
            if not role:
                raise ValueError(f"Role '{role_name}' does not exist.")
            role_id = role["id"]

            cursor.execute(
                """
                INSERT INTO users (username, password_hash, full_name, email, role_id, is_active)
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (clean_username, password_hash, clean_name, email.strip() if email else None, role_id, is_active)
            )
            return cursor.lastrowid
    except sqlite3.IntegrityError as e:
        raise InvalidOperationError(f"User '{clean_username}' could not be created: {e}") from e
    finally:
        conn.close()


def update_user_record(
    user_id: int,
    full_name: Optional[str] = None,
    email: Optional[str] = None,
    role_name: Optional[str] = None,
    is_active: Optional[int] = None,
    db_path: Optional[str] = None
) -> None:
    """Updates profile, role, or active status for a user."""
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            updates = []
            params = []
            if full_name is not None:
                updates.append("full_name = ?")
                params.append(full_name.strip())
            if email is not None:
                updates.append("email = ?")
                params.append(email.strip() if email else None)
            if role_name is not None:
                cursor.execute("SELECT id FROM roles WHERE name = ? COLLATE NOCASE;", (role_name.strip(),))
                role = cursor.fetchone()
                if not role:
                    raise ValueError(f"Role '{role_name}' does not exist.")
                updates.append("role_id = ?")
                params.append(role["id"])
            if is_active is not None:
                if is_active not in (0, 1):
                    raise ValueError("is_active must be 0 or 1.")
                updates.append("is_active = ?")
                params.append(is_active)

            if not updates:
                return

            now_ts = datetime.now().strftime(DATETIME_FORMAT)
            updates.append("updated_at = ?")
            params.append(now_ts)
            params.append(user_id)

            cursor.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?;", params)
            if cursor.rowcount == 0:
                raise UserNotFoundError(f"User with ID {user_id} not found.")
    finally:
        conn.close()


def update_user_password(user_id: int, new_password_hash: str, db_path: Optional[str] = None) -> None:
    """Updates password hash for a user."""
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            now_ts = datetime.now().strftime(DATETIME_FORMAT)
            cursor.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?;",
                (new_password_hash, now_ts, user_id)
            )
            if cursor.rowcount == 0:
                raise UserNotFoundError(f"User with ID {user_id} not found.")
    finally:
        conn.close()


def record_user_login(user_id: int, db_path: Optional[str] = None) -> None:
    """Updates last_login timestamp when a user signs in."""
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            now_ts = datetime.now().strftime(DATETIME_FORMAT)
            cursor.execute("UPDATE users SET last_login = ? WHERE id = ?;", (now_ts, user_id))
    finally:
        conn.close()


def get_all_roles(db_path: Optional[str] = None) -> List[sqlite3.Row]:
    """Retrieves all defined roles."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM roles ORDER BY id ASC;")
        return cursor.fetchall()
    finally:
        conn.close()


def get_role_by_name(role_name: str, db_path: Optional[str] = None) -> Optional[sqlite3.Row]:
    """Retrieves role by name."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM roles WHERE name = ? COLLATE NOCASE;", (role_name.strip(),))
        return cursor.fetchone()
    finally:
        conn.close()


def get_permissions_for_role(role_id_or_name: Any, db_path: Optional[str] = None) -> List[str]:
    """Retrieves permission codes mapped to a given role ID or name."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        if isinstance(role_id_or_name, int):
            cursor.execute(
                """
                SELECT p.code
                FROM permissions p
                JOIN role_permissions rp ON p.id = rp.permission_id
                WHERE rp.role_id = ?;
                """,
                (role_id_or_name,)
            )
        else:
            cursor.execute(
                """
                SELECT p.code
                FROM permissions p
                JOIN role_permissions rp ON p.id = rp.permission_id
                JOIN roles r ON rp.role_id = r.id
                WHERE r.name = ? COLLATE NOCASE;
                """,
                (str(role_id_or_name).strip(),)
            )
        return [row["code"] for row in cursor.fetchall()]
    finally:
        conn.close()


# ============================================================================
# Activity Logs
# ============================================================================

def log_activity_event(
    user_id: Optional[int],
    username: Optional[str],
    action: str,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    details: Optional[str] = None,
    db_path: Optional[str] = None
) -> int:
    """Records an activity event in the audit trail."""
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO activity_logs (user_id, username, action, entity_type, entity_id, details)
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (user_id, username, action, entity_type, str(entity_id) if entity_id else None, details)
            )
            return cursor.lastrowid
    finally:
        conn.close()


def get_activity_logs(
    limit: int = 100,
    action_filter: Optional[str] = None,
    user_filter: Optional[str] = None,
    db_path: Optional[str] = None
) -> List[sqlite3.Row]:
    """Retrieves audit trail activity logs ordered by timestamp descending."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        conditions = []
        params = []
        if action_filter:
            conditions.append("action = ?")
            params.append(action_filter)
        if user_filter:
            conditions.append("(username = ? COLLATE NOCASE OR user_id = ?)")
            params.extend([user_filter, user_filter])

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT * FROM activity_logs {where_clause} ORDER BY timestamp DESC LIMIT ?;"
        params.append(limit)
        cursor.execute(sql, params)
        return cursor.fetchall()
    finally:
        conn.close()


# ============================================================================
# Valet Management Operations
# ============================================================================

def create_valet_assignment(
    session_id: int,
    valet_user_id: Optional[int] = None,
    assigned_by_user_id: Optional[int] = None,
    notes: Optional[str] = None,
    db_path: Optional[str] = None
) -> int:
    """Creates a new valet assignment linked to a parking session."""
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO valet_assignments (session_id, valet_user_id, assigned_by_user_id, status, notes)
                VALUES (?, ?, ?, 'ASSIGNED', ?);
                """,
                (session_id, valet_user_id, assigned_by_user_id, notes)
            )
            assignment_id = cursor.lastrowid

            cursor.execute(
                """
                INSERT INTO valet_activity_logs (assignment_id, valet_user_id, status_from, status_to, notes)
                VALUES (?, ?, NULL, 'ASSIGNED', ?);
                """,
                (assignment_id, valet_user_id, notes or "Assignment created")
            )
            return assignment_id
    finally:
        conn.close()


def update_valet_status(
    assignment_id: int,
    new_status: str,
    valet_user_id: Optional[int] = None,
    notes: Optional[str] = None,
    db_path: Optional[str] = None
) -> None:
    """
    Transitions valet assignment status along lifecycle:
    ASSIGNED -> VEHICLE_RECEIVED -> PARKING_IN_PROGRESS -> PARKED
    -> RETRIEVAL_REQUESTED -> VEHICLE_RETRIEVED -> DELIVERED
    """
    valid_statuses = (
        'ASSIGNED',
        'VEHICLE_RECEIVED',
        'PARKING_IN_PROGRESS',
        'PARKED',
        'RETRIEVAL_REQUESTED',
        'VEHICLE_RETRIEVED',
        'DELIVERED'
    )
    if new_status not in valid_statuses:
        raise ValueError(f"Invalid valet status '{new_status}'. Must be one of {valid_statuses}")

    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM valet_assignments WHERE id = ?;", (assignment_id,))
            assignment = cursor.fetchone()
            if not assignment:
                raise ValetAssignmentNotFoundError(f"Valet assignment with ID {assignment_id} not found.")

            old_status = assignment["status"]
            allowed_transitions = {
                "ASSIGNED": {"VEHICLE_RECEIVED"},
                "VEHICLE_RECEIVED": {"PARKING_IN_PROGRESS"},
                "PARKING_IN_PROGRESS": {"PARKED"},
                "PARKED": {"RETRIEVAL_REQUESTED"},
                "RETRIEVAL_REQUESTED": {"VEHICLE_RETRIEVED"},
                "VEHICLE_RETRIEVED": {"DELIVERED"},
                "DELIVERED": set(),
            }
            if new_status not in allowed_transitions[old_status]:
                raise InvalidOperationError(
                    f"Invalid valet transition from '{old_status}' to '{new_status}'."
                )
            now_ts = datetime.now().strftime(DATETIME_FORMAT)

            # Build timestamp updates based on target status
            updates = ["status = ?", "updated_at = ?"]
            params = [new_status, now_ts]
            if valet_user_id is not None:
                updates.append("valet_user_id = ?")
                params.append(valet_user_id)
            if notes is not None:
                updates.append("notes = ?")
                params.append(notes)

            if new_status == "VEHICLE_RECEIVED" and not assignment["received_at"]:
                updates.append("received_at = ?")
                params.append(now_ts)
            elif new_status == "PARKED" and not assignment["parked_at"]:
                updates.append("parked_at = ?")
                params.append(now_ts)
            elif new_status == "RETRIEVAL_REQUESTED" and not assignment["retrieval_requested_at"]:
                updates.append("retrieval_requested_at = ?")
                params.append(now_ts)
            elif new_status == "VEHICLE_RETRIEVED" and not assignment["retrieved_at"]:
                updates.append("retrieved_at = ?")
                params.append(now_ts)
            elif new_status == "DELIVERED" and not assignment["delivered_at"]:
                updates.append("delivered_at = ?")
                params.append(now_ts)

            params.append(assignment_id)
            cursor.execute(f"UPDATE valet_assignments SET {', '.join(updates)} WHERE id = ?;", params)

            # Log transition in valet_activity_logs
            active_valet_id = valet_user_id or assignment["valet_user_id"]
            cursor.execute(
                """
                INSERT INTO valet_activity_logs (assignment_id, valet_user_id, status_from, status_to, notes)
                VALUES (?, ?, ?, ?, ?);
                """,
                (assignment_id, active_valet_id, old_status, new_status, notes)
            )
    finally:
        conn.close()


def get_valet_assignments(
    valet_user_id: Optional[int] = None,
    status_filter: Optional[str] = None,
    db_path: Optional[str] = None
) -> List[sqlite3.Row]:
    """Retrieves valet assignments with joined vehicle, slot, floor, and user details."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        conditions = []
        params = []
        if valet_user_id is not None:
            conditions.append("va.valet_user_id = ?")
            params.append(valet_user_id)
        if status_filter:
            conditions.append("va.status = ?")
            params.append(status_filter)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"""
            SELECT 
                va.*,
                ps.vehicle_registration_number,
                ps.vehicle_type,
                ps.owner_name,
                ps.owner_contact,
                ps.entry_time,
                ps.exit_time,
                ps.status AS session_status,
                s.slot_number,
                f.floor_identifier,
                f.floor_name,
                u.username AS valet_username,
                u.full_name AS valet_full_name,
                ab.username AS assigned_by_username
            FROM valet_assignments va
            JOIN parking_sessions ps ON va.session_id = ps.id
            JOIN parking_slots s ON ps.slot_id = s.id
            LEFT JOIN floors f ON s.floor_id = f.id
            LEFT JOIN users u ON va.valet_user_id = u.id
            LEFT JOIN users ab ON va.assigned_by_user_id = ab.id
            {where_clause}
            ORDER BY va.assigned_at DESC;
        """
        cursor.execute(sql, params)
        return cursor.fetchall()
    finally:
        conn.close()


def get_valet_assignment_by_id(assignment_id: int, db_path: Optional[str] = None) -> Optional[sqlite3.Row]:
    """Retrieves a single valet assignment with complete joined context."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT 
                va.*,
                ps.vehicle_registration_number,
                ps.vehicle_type,
                ps.owner_name,
                ps.owner_contact,
                ps.entry_time,
                ps.exit_time,
                ps.status AS session_status,
                s.slot_number,
                f.floor_identifier,
                f.floor_name,
                u.username AS valet_username,
                u.full_name AS valet_full_name
            FROM valet_assignments va
            JOIN parking_sessions ps ON va.session_id = ps.id
            JOIN parking_slots s ON ps.slot_id = s.id
            LEFT JOIN floors f ON s.floor_id = f.id
            LEFT JOIN users u ON va.valet_user_id = u.id
            WHERE va.id = ?;
            """,
            (assignment_id,)
        )
        return cursor.fetchone()
    finally:
        conn.close()


def get_valet_assignment_by_session_id(session_id: int, db_path: Optional[str] = None) -> Optional[sqlite3.Row]:
    """Retrieves the active or most recent valet assignment for a parking session."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT 
                va.*,
                ps.vehicle_registration_number,
                ps.vehicle_type,
                s.slot_number,
                f.floor_identifier,
                f.floor_name,
                u.username AS valet_username
            FROM valet_assignments va
            JOIN parking_sessions ps ON va.session_id = ps.id
            JOIN parking_slots s ON ps.slot_id = s.id
            LEFT JOIN floors f ON s.floor_id = f.id
            LEFT JOIN users u ON va.valet_user_id = u.id
            WHERE va.session_id = ?
            ORDER BY va.id DESC
            LIMIT 1;
            """,
            (session_id,)
        )
        return cursor.fetchone()
    finally:
        conn.close()


def get_valet_activity_logs(
    assignment_id: Optional[int] = None,
    valet_user_id: Optional[int] = None,
    db_path: Optional[str] = None
) -> List[sqlite3.Row]:
    """Retrieves history of valet status transitions."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        conditions = []
        params = []
        if assignment_id is not None:
            conditions.append("val.assignment_id = ?")
            params.append(assignment_id)
        if valet_user_id is not None:
            conditions.append("val.valet_user_id = ?")
            params.append(valet_user_id)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"""
            SELECT val.*, u.username AS valet_username
            FROM valet_activity_logs val
            LEFT JOIN users u ON val.valet_user_id = u.id
            {where_clause}
            ORDER BY val.timestamp DESC;
        """
        cursor.execute(sql, params)
        return cursor.fetchall()
    finally:
        conn.close()


# ============================================================================
# Security Flag Operations
# ============================================================================

def flag_vehicle(
    vehicle_registration_number: str,
    flagged_by_user_id: Optional[int],
    reason: str,
    severity: str = "MEDIUM",
    db_path: Optional[str] = None
) -> int:
    """Flags a vehicle as suspicious or requiring security inspection."""
    clean_plate = vehicle_registration_number.strip().upper()
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO security_flags (vehicle_registration_number, flagged_by_user_id, reason, severity, status)
                VALUES (?, ?, ?, ?, 'ACTIVE');
                """,
                (clean_plate, flagged_by_user_id, reason.strip(), severity.upper())
            )
            return cursor.lastrowid
    finally:
        conn.close()


def get_vehicle_flags(
    vehicle_registration_number: Optional[str] = None,
    active_only: bool = True,
    db_path: Optional[str] = None
) -> List[sqlite3.Row]:
    """Retrieves security flags, optionally filtered by vehicle plate."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        conditions = []
        params = []
        if vehicle_registration_number:
            conditions.append("sf.vehicle_registration_number = ? COLLATE NOCASE")
            params.append(vehicle_registration_number.strip().upper())
        if active_only:
            conditions.append("sf.status = 'ACTIVE'")

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"""
            SELECT sf.*, u.username AS flagged_by_username
            FROM security_flags sf
            LEFT JOIN users u ON sf.flagged_by_user_id = u.id
            {where_clause}
            ORDER BY sf.created_at DESC;
        """
        cursor.execute(sql, params)
        return cursor.fetchall()
    finally:
        conn.close()


def resolve_vehicle_flag(flag_id: int, resolution_notes: str, db_path: Optional[str] = None) -> None:
    """Marks a security flag as resolved."""
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            now_ts = datetime.now().strftime(DATETIME_FORMAT)
            cursor.execute(
                """
                UPDATE security_flags
                SET status = 'RESOLVED', resolution_notes = ?, resolved_at = ?
                WHERE id = ?;
                """,
                (resolution_notes.strip(), now_ts, flag_id)
            )
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
    c.execute("SELECT COUNT(*) AS total_floors FROM floors;")
    floors_cnt = c.fetchone()["total_floors"]
    c.execute("SELECT COUNT(*) AS total_users FROM users;")
    users_cnt = c.fetchone()["total_users"]
    conn.close()
    print("Database initialization successful.")
    print(f"Parking Floors seeded: {floors_cnt}")
    print(f"Parking Slots seeded: {slots_cnt}")
    print(f"Users seeded: {users_cnt}")
