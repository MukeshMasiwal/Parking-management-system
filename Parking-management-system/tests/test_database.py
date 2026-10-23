"""
Unit Tests for SQLite Database Layer
====================================
Tests all requirements, constraints, transactions, and edge cases
using isolated temporary databases (both in-memory and temp file on disk).
"""

import os
import sys
import tempfile
import unittest
import sqlite3
from datetime import datetime, timedelta

# Ensure parent directory is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import database
from database import (
    init_db,
    get_connection,
    get_all_slots,
    get_available_slots,
    get_slot_by_id,
    get_slot_by_number,
    create_slot,
    update_slot_status,
    start_parking_session,
    end_parking_session,
    cancel_parking_session,
    get_session_by_id,
    get_active_session_by_vehicle,
    get_all_active_sessions,
    get_completed_sessions,
    get_session_history,
    calculate_fee,
    get_setting,
    set_setting,
    get_all_settings,
    log_defect,
    get_all_defects,
    get_defect_counts_by_category,
    create_parking_record,
    get_all_active_parking_records,
    update_parking_record_exit,
    delete_parking_record,
    DatabaseError,
    SlotNotAvailableError,
    ActiveSessionExistsError,
    SessionNotFoundError,
    SlotNotFoundError,
    InvalidOperationError,
)


class TestDatabaseLayer(unittest.TestCase):
    """Test suite for the SQLite database layer using temporary databases."""

    def setUp(self):
        """Create a new temporary database for each test case."""
        self.temp_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_file.close()
        self.db_path = self.temp_file.name
        # Initialize schema and seed data in temporary database
        init_db(self.db_path)

    def tearDown(self):
        """Clean up the temporary database file."""
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except OSError:
                pass

    # ------------------------------------------------------------------------
    # 1. Database Initialization & Schema Verification
    # ------------------------------------------------------------------------

    def test_schema_initialization(self):
        """Verify that all required tables, indexes, and views are created."""
        conn = get_connection(self.db_path)
        cursor = conn.cursor()

        # Check tables existence
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row["name"] for row in cursor.fetchall()}
        self.assertIn("parking_slots", tables)
        self.assertIn("parking_sessions", tables)
        self.assertIn("application_settings", tables)
        self.assertIn("defects", tables)

        # Check view existence
        cursor.execute("SELECT name FROM sqlite_master WHERE type='view';")
        views = {row["name"] for row in cursor.fetchall()}
        self.assertIn("parking_records", views)

        # Check partial unique indexes
        cursor.execute("SELECT name FROM sqlite_master WHERE type='index';")
        indexes = {row["name"] for row in cursor.fetchall()}
        self.assertIn("idx_unique_active_vehicle", indexes)
        self.assertIn("idx_unique_active_slot", indexes)

        conn.close()

    def test_idempotent_initialization_preserves_existing_data(self):
        """Verify that running init_db repeatedly does NOT delete or overwrite existing data."""
        # Insert a custom slot and custom setting
        create_slot("Z-99", "Standard", db_path=self.db_path)
        set_setting("custom_test_key", "custom_val", db_path=self.db_path)

        # Run init_db again on the existing database
        init_db(self.db_path)

        # Verify data is still intact
        slot = get_slot_by_number("Z-99", db_path=self.db_path)
        self.assertIsNotNone(slot)
        self.assertEqual(slot["slot_number"], "Z-99")

        val = get_setting("custom_test_key", db_path=self.db_path)
        self.assertEqual(val, "custom_val")

    def test_foreign_keys_enforced(self):
        """Verify that PRAGMA foreign_keys is ON and foreign key violations are rejected."""
        conn = get_connection(self.db_path)
        cursor = conn.cursor()

        cursor.execute("PRAGMA foreign_keys;")
        fk_status = cursor.fetchone()[0]
        self.assertEqual(fk_status, 1)

        # Attempt to insert a session with a non-existent slot_id directly
        with self.assertRaises(sqlite3.IntegrityError):
            with conn:
                cursor.execute(
                    """
                    INSERT INTO parking_sessions (
                        vehicle_registration_number, slot_id, status
                    ) VALUES ('TEST-999', 999999, 'ACTIVE');
                    """
                )
        conn.close()

    # ------------------------------------------------------------------------
    # 2. Parking Slots Management
    # ------------------------------------------------------------------------

    def test_parking_slots_seeded(self):
        """Verify default parking slots are seeded properly on fresh database."""
        slots = get_all_slots(self.db_path)
        self.assertEqual(len(slots), 50)
        available = get_available_slots(self.db_path)
        self.assertEqual(len(available), 50)

    def test_get_slots_by_type(self):
        """Verify filtering available slots by slot type."""
        two_wheelers = get_available_slots(self.db_path, slot_type="Two-Wheeler")
        self.assertEqual(len(two_wheelers), 10)
        ev_slots = get_available_slots(self.db_path, slot_type="EV")
        self.assertEqual(len(ev_slots), 5)

    def test_create_and_find_slot(self):
        """Verify custom slot creation and lookup."""
        slot_id = create_slot("VIP-01", "Standard", db_path=self.db_path)
        self.assertIsInstance(slot_id, int)

        slot = get_slot_by_number("vip-01", db_path=self.db_path)
        self.assertIsNotNone(slot)
        self.assertEqual(slot["slot_number"], "VIP-01")
        self.assertEqual(slot["status"], "Available")
        self.assertEqual(
            get_slot_by_number("VIP-01", db_path=self.db_path)["id"],
            slot["id"],
        )

    def test_blank_slot_lookup_returns_no_result(self):
        """Blank slot identifiers must not match an existing record."""
        self.assertIsNone(get_slot_by_number("   ", db_path=self.db_path))

    def test_duplicate_slot_number_rejected(self):
        """Verify duplicate slot number raises an error."""
        with self.assertRaises(InvalidOperationError):
            create_slot("A-01", "Standard", db_path=self.db_path)

    # ------------------------------------------------------------------------
    # 3. Parking Sessions & Core Requirements
    # ------------------------------------------------------------------------

    def test_start_and_end_parking_session(self):
        """Verify starting and ending a parking session, including slot status transition."""
        available = get_available_slots(self.db_path)
        slot = available[0]
        slot_id = slot["id"]

        # Start session
        session_id = start_parking_session(
            vehicle_registration_number="MH12AB1234",
            slot_id=slot_id,
            vehicle_type="Car",
            owner_name="John Doe",
            owner_contact="+91-9876543210",
            entry_time="2026-10-11 10:00:00",
            db_path=self.db_path
        )
        self.assertIsInstance(session_id, int)

        # Verify session is stored with all fields
        session = get_session_by_id(session_id, db_path=self.db_path)
        self.assertIsNotNone(session)
        self.assertEqual(session["vehicle_registration_number"], "MH12AB1234")
        self.assertEqual(session["vehicle_type"], "Car")
        self.assertEqual(session["owner_name"], "John Doe")
        self.assertEqual(session["owner_contact"], "+91-9876543210")
        self.assertEqual(session["status"], "ACTIVE")
        self.assertEqual(session["slot_id"], slot_id)
        self.assertEqual(session["entry_time"], "2026-10-11 10:00:00")
        self.assertIsNone(session["exit_time"])

        # Verify slot status changed to Occupied
        slot_updated = get_slot_by_id(slot_id, db_path=self.db_path)
        self.assertEqual(slot_updated["status"], "Occupied")

        # End session after 2 hours (120 minutes)
        result = end_parking_session(
            session_id=session_id,
            exit_time="2026-10-11 12:00:00",
            db_path=self.db_path
        )
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["fee"], 40.0)  # 2 hours * $20.0

        # Verify session is marked COMPLETED in database
        session_after = get_session_by_id(session_id, db_path=self.db_path)
        self.assertEqual(session_after["status"], "COMPLETED")
        self.assertEqual(session_after["exit_time"], "2026-10-11 12:00:00")
        self.assertEqual(session_after["fee"], 40.0)

        # Verify slot is freed back to Available
        slot_freed = get_slot_by_id(slot_id, db_path=self.db_path)
        self.assertEqual(slot_freed["status"], "Available")

    def test_prevent_duplicate_active_session_for_same_vehicle(self):
        """Requirement 8: Prevent duplicate active parking sessions for the same vehicle."""
        slots = get_available_slots(self.db_path)
        slot1 = slots[0]["id"]
        slot2 = slots[1]["id"]

        # First session for vehicle
        start_parking_session(
            vehicle_registration_number="DL01XY9999",
            slot_id=slot1,
            db_path=self.db_path
        )

        # Attempt second active session for same vehicle in a different slot
        with self.assertRaises(ActiveSessionExistsError):
            start_parking_session(
                vehicle_registration_number="DL01XY9999",
                slot_id=slot2,
                db_path=self.db_path
            )

        # Verify case-insensitivity ('dl01xy9999')
        with self.assertRaises(ActiveSessionExistsError):
            start_parking_session(
                vehicle_registration_number="dl01xy9999",
                slot_id=slot2,
                db_path=self.db_path
            )

    def test_prevent_multiple_active_occupants_for_same_slot(self):
        """Requirement 9: Prevent a parking slot from having multiple active occupants."""
        slots = get_available_slots(self.db_path)
        target_slot = slots[0]["id"]

        # Vehicle 1 occupies slot
        start_parking_session(
            vehicle_registration_number="KA03AA1111",
            slot_id=target_slot,
            db_path=self.db_path
        )

        # Vehicle 2 attempts to occupy the same slot
        with self.assertRaises(SlotNotAvailableError):
            start_parking_session(
                vehicle_registration_number="KA03BB2222",
                slot_id=target_slot,
                db_path=self.db_path
            )

    def test_preserve_completed_parking_history(self):
        """Requirement 7: Preserve completed parking history and allow re-parking."""
        slots = get_available_slots(self.db_path)
        slot1 = slots[0]["id"]

        # 1. Park vehicle
        s1 = start_parking_session(
            vehicle_registration_number="UP16CC3333",
            slot_id=slot1,
            entry_time="2026-10-10 09:00:00",
            db_path=self.db_path
        )

        # 2. Complete session
        end_parking_session(
            session_id=s1,
            exit_time="2026-10-10 11:00:00",
            fee=40.0,
            db_path=self.db_path
        )

        # 3. Vehicle parks again the next day
        s2 = start_parking_session(
            vehicle_registration_number="UP16CC3333",
            slot_id=slot1,
            entry_time="2026-10-11 09:00:00",
            db_path=self.db_path
        )

        # Complete second session
        end_parking_session(
            session_id=s2,
            exit_time="2026-10-11 10:30:00",
            fee=40.0,
            db_path=self.db_path
        )

        # 4. Verify history is fully preserved (both sessions exist)
        history = get_session_history(db_path=self.db_path, vehicle_number="UP16CC3333")
        self.assertEqual(len(history), 2)
        session_ids = [row["id"] for row in history]
        self.assertIn(s1, session_ids)
        self.assertIn(s2, session_ids)

        for row in history:
            self.assertEqual(row["status"], "COMPLETED")
            self.assertIsNotNone(row["exit_time"])
            self.assertGreater(row["fee"], 0.0)

    def test_cancel_parking_session(self):
        """Verify cancelling an active session frees the slot and records status."""
        slots = get_available_slots(self.db_path)
        slot_id = slots[0]["id"]

        session_id = start_parking_session(
            vehicle_registration_number="HR26DD4444",
            slot_id=slot_id,
            db_path=self.db_path
        )

        cancel_parking_session(session_id, db_path=self.db_path)

        session = get_session_by_id(session_id, db_path=self.db_path)
        self.assertEqual(session["status"], "CANCELLED")

        slot = get_slot_by_id(slot_id, db_path=self.db_path)
        self.assertEqual(slot["status"], "Available")

    # ------------------------------------------------------------------------
    # 4. Fee Calculation Engine
    # ------------------------------------------------------------------------

    def test_fee_calculation_grace_period(self):
        """Verify that duration within grace period incurs zero fee."""
        entry_t = "2026-10-11 10:00:00"
        exit_t = "2026-10-11 10:12:00"  # 12 minutes <= 15 min grace period
        fee = calculate_fee(entry_t, exit_t, vehicle_type="Car", db_path=self.db_path)
        self.assertEqual(fee, 0.0)

    def test_fee_calculation_hourly_tiers(self):
        """Verify fee scales by vehicle type and billable hours."""
        entry_t = "2026-10-11 10:00:00"
        # 1 hour 15 minutes = 2 billable hours
        exit_t = "2026-10-11 11:15:00"

        # Car: 2 hours * $20 = $40
        car_fee = calculate_fee(entry_t, exit_t, vehicle_type="Car", db_path=self.db_path)
        self.assertEqual(car_fee, 40.0)

        # Two-Wheeler: 2 hours * $10 = $20
        bike_fee = calculate_fee(entry_t, exit_t, vehicle_type="Two-Wheeler", db_path=self.db_path)
        self.assertEqual(bike_fee, 20.0)

        # Heavy Vehicle: 2 hours * $40 = $80
        heavy_fee = calculate_fee(entry_t, exit_t, vehicle_type="Heavy", db_path=self.db_path)
        self.assertEqual(heavy_fee, 80.0)

    def test_fee_calculation_invalid_chronology(self):
        """Verify exit time prior to entry time raises ValueError."""
        entry_t = "2026-10-11 12:00:00"
        exit_t = "2026-10-11 10:00:00"
        with self.assertRaises(ValueError):
            calculate_fee(entry_t, exit_t, db_path=self.db_path)

    # ------------------------------------------------------------------------
    # 5. Application Settings
    # ------------------------------------------------------------------------

    def test_application_settings_crud(self):
        """Verify reading, creating, and updating application settings."""
        # Read default setting
        hourly_rate = get_setting("hourly_rate", db_path=self.db_path)
        self.assertEqual(hourly_rate, "20.0")

        # Update setting
        set_setting("hourly_rate", "25.0", description="Revised rate", db_path=self.db_path)
        self.assertEqual(get_setting("hourly_rate", db_path=self.db_path), "25.0")

        # Insert new setting
        set_setting("night_surcharge", "5.0", db_path=self.db_path)
        self.assertEqual(get_setting("night_surcharge", db_path=self.db_path), "5.0")

        all_settings = get_all_settings(self.db_path)
        self.assertIn("night_surcharge", all_settings)
        self.assertEqual(all_settings["hourly_rate"], "25.0")

    def test_missing_setting_returns_none(self):
        """Unknown configuration keys should be distinguishable from empty values."""
        self.assertIsNone(get_setting("missing_setting", db_path=self.db_path))

    def test_capacity_metrics_start_empty(self):
        metrics = database.get_capacity_metrics(db_path=self.db_path)
        self.assertEqual(metrics["total_slots"], 50)
        self.assertEqual(metrics["occupied_slots"], 0)
        self.assertEqual(metrics["available_slots"], 50)

    def test_application_settings_negative_rate_rejected(self):
        """Verify that negative rate or capacity values raise ValueError."""
        with self.assertRaises(ValueError):
            set_setting("hourly_rate", "-10.0", db_path=self.db_path)
        with self.assertRaises(ValueError):
            set_setting("rate_car", "-20.0", db_path=self.db_path)
        with self.assertRaises(ValueError):
            set_setting("grace_period_mins", "-5", db_path=self.db_path)
        with self.assertRaises(ValueError):
            set_setting("hourly_rate", "non-numeric", db_path=self.db_path)

    # ------------------------------------------------------------------------
    # 6. TQM Defect Logging
    # ------------------------------------------------------------------------

    def test_log_defect_and_pareto_aggregation(self):
        """Verify TQM defect logging, severity constraints, and category counts."""
        log_defect("Software", "Typo in checkout dialog", 2, db_path=self.db_path)
        log_defect("Software", "Lag when opening records", 3, db_path=self.db_path)
        log_defect("Facility", "Barrier gate arm slow", 4, db_path=self.db_path)

        defects = get_all_defects(self.db_path)
        self.assertEqual(len(defects), 3)

        counts = get_defect_counts_by_category(self.db_path)
        # Software has 2, Facility has 1
        self.assertEqual(counts[0]["category"], "Software")
        self.assertEqual(counts[0]["count"], 2)
        self.assertEqual(counts[1]["category"], "Facility")
        self.assertEqual(counts[1]["count"], 1)

    def test_defect_severity_constraint(self):
        """Verify severity outside 1-5 is rejected."""
        with self.assertRaises(ValueError):
            log_defect("Software", "Invalid severity test", 6, db_path=self.db_path)

    # ------------------------------------------------------------------------
    # 7. Backward Compatibility (Legacy Wrapper Interface & View)
    # ------------------------------------------------------------------------

    def test_legacy_interface_and_view(self):
        """Verify legacy CRUD wrappers and the parking_records SQL view."""
        # Create record via legacy function
        record_id = create_parking_record("RJ14EE5555", vehicle_type="Car", db_path=self.db_path)
        self.assertIsInstance(record_id, int)

        active = get_all_active_parking_records(self.db_path)
        self.assertTrue(any(r["vehicle_registration_number"] == "RJ14EE5555" for r in active))

        # Query via backward-compatible SQL view
        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM parking_records WHERE vehicle_number = 'RJ14EE5555';")
        view_row = cursor.fetchone()
        self.assertIsNotNone(view_row)
        self.assertEqual(view_row["vehicle_number"], "RJ14EE5555")
        self.assertEqual(view_row["status"], "ACTIVE")
        self.assertIsNotNone(view_row["slot_number"])
        conn.close()

        # Update exit via legacy function
        res = update_parking_record_exit(record_id, fee=30.0, db_path=self.db_path)
        self.assertEqual(res["status"], "COMPLETED")
        self.assertEqual(res["fee"], 30.0)

    def test_search_sessions_filters_by_slot_number(self):
        """Slot filters must return only sessions assigned to that bay."""
        first = get_available_slots(self.db_path)[0]
        second = get_available_slots(self.db_path)[1]
        first_id = start_parking_session(
            "SLOT-FILTER-01",
            first["id"],
            entry_time="2026-10-11 08:00:00",
            db_path=self.db_path,
        )
        start_parking_session(
            "SLOT-FILTER-02",
            second["id"],
            entry_time="2026-10-11 08:00:00",
            db_path=self.db_path,
        )

        results = database.search_sessions(
            slot_number=first["slot_number"],
            db_path=self.db_path,
        )
        self.assertEqual([row["id"] for row in results], [first_id])


if __name__ == "__main__":
    unittest.main()
