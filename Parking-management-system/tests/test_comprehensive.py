"""
Comprehensive Automated Tests — Parking Management System
==========================================================

Covers all 14 required test topics:
 1.  Database initialization
 2.  Valid vehicle registration
 3.  Invalid required fields
 4.  Duplicate active vehicle prevention
 5.  Parking capacity enforcement
 6.  Parking slot assignment
 7.  Vehicle search
 8.  Permitted record updates
 9.  Safe deletion rules
10.  Parking duration and fee calculation
11.  Vehicle exit
12.  Slot release
13.  Historical record preservation
14.  Database error handling

Testing conventions:
- Every test uses a fresh temporary SQLite file (setUp/tearDown).
- No real application database is touched.
- All timestamps are fixed strings — no dependency on wall-clock time.
- Validation is NOT weakened; tests assert that real constraints fire.
"""

import os
import sys
import sqlite3
import tempfile
import unittest

# Make the project root importable from the tests/ sub-directory
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import database
import parking_service


# ---------------------------------------------------------------------------
# Shared test base — creates and tears down an isolated temp database
# ---------------------------------------------------------------------------

class _TempDBMixin(unittest.TestCase):
    """Mixin that wires up a fresh temporary SQLite database for every test."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db = self._tmp.name
        database.init_db(self.db)

    def tearDown(self):
        try:
            os.remove(self.db)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def _park(self, plate, vehicle_type="Car", owner_name=None,
              entry_time="2026-01-01 08:00:00", slot_id=None, slot_number=None):
        """Register a vehicle entry with controlled timestamps."""
        return parking_service.register_vehicle_entry(
            vehicle_registration_number=plate,
            vehicle_type=vehicle_type,
            owner_name=owner_name,
            entry_time=entry_time,
            slot_id=slot_id,
            slot_number=slot_number,
            db_path=self.db,
        )

    def _exit(self, session_id=None, plate=None, exit_time="2026-01-01 10:00:00",
              discount=0.0):
        """Record a vehicle exit with a controlled exit timestamp."""
        return parking_service.record_vehicle_exit(
            session_id=session_id,
            vehicle_registration_number=plate,
            exit_time=exit_time,
            discount_amount=discount,
            db_path=self.db,
        )

    def _first_available_slot(self):
        return database.get_available_slots(self.db)[0]

    def _lock_all_slots_except_first(self):
        """Put all slots except the very first into Maintenance."""
        all_slots = database.get_all_slots(self.db)
        for slot in all_slots[1:]:
            database.update_slot_status(slot["id"], "Maintenance", self.db)

    def _fill_all_slots(self):
        """Park a unique vehicle in every available slot."""
        available = database.get_available_slots(self.db)
        sessions = []
        for i, slot in enumerate(available):
            entry = self._park(f"XX{i:02d}AA{i+1:04d}", slot_id=slot["id"],
                               entry_time="2026-01-01 06:00:00")
            sessions.append(entry)
        return sessions


# ===========================================================================
# 1. Database Initialization
# ===========================================================================

class TestDatabaseInitialization(_TempDBMixin):
    """Verify schema creation, idempotency, default seed data, and foreign keys."""

    def test_required_tables_created(self):
        """All four core tables must exist after init_db."""
        conn = database.get_connection(self.db)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row["name"] for row in cur.fetchall()}
        conn.close()
        for required in ("parking_slots", "parking_sessions",
                         "application_settings", "defects"):
            with self.subTest(table=required):
                self.assertIn(required, tables)

    def test_backward_compat_view_exists(self):
        """The parking_records view must exist for legacy compatibility."""
        conn = database.get_connection(self.db)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='view';")
        views = {row["name"] for row in cur.fetchall()}
        conn.close()
        self.assertIn("parking_records", views)

    def test_partial_unique_indexes_exist(self):
        """Unique partial indexes protecting duplicate-active constraints must exist."""
        conn = database.get_connection(self.db)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='index';")
        indexes = {row["name"] for row in cur.fetchall()}
        conn.close()
        self.assertIn("idx_unique_active_vehicle", indexes)
        self.assertIn("idx_unique_active_slot", indexes)

    def test_default_slots_seeded(self):
        """50 default slots must be created on a fresh database."""
        slots = database.get_all_slots(self.db)
        self.assertEqual(len(slots), 50)

    def test_default_settings_seeded(self):
        """Core billing settings must be present after initialization."""
        for key in ("hourly_rate", "rate_car", "rate_two_wheeler",
                    "rate_heavy", "grace_period_mins", "total_capacity"):
            with self.subTest(setting=key):
                val = database.get_setting(key, db_path=self.db)
                self.assertIsNotNone(val)

    def test_idempotent_reinit_preserves_data(self):
        """Running init_db a second time must not overwrite existing rows."""
        database.create_slot("REINIT-TEST", "Standard", db_path=self.db)
        database.set_setting("my_custom_key", "custom_value", db_path=self.db)

        # Re-run initialization
        database.init_db(self.db)

        slot = database.get_slot_by_number("REINIT-TEST", db_path=self.db)
        self.assertIsNotNone(slot, "Custom slot was wiped by re-initialization")

        val = database.get_setting("my_custom_key", db_path=self.db)
        self.assertEqual(val, "custom_value", "Custom setting was wiped by re-initialization")

    def test_foreign_keys_pragma_is_on(self):
        """PRAGMA foreign_keys must be 1 for every new connection."""
        conn = database.get_connection(self.db)
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys;")
        self.assertEqual(cur.fetchone()[0], 1)
        conn.close()

    def test_slot_types_seeded_correctly(self):
        """Verify slot type distribution matches DEFAULT_SLOT_CONFIGS."""
        standard = database.get_available_slots(self.db, slot_type="Standard")
        compact = database.get_available_slots(self.db, slot_type="Compact")
        two_wheeler = database.get_available_slots(self.db, slot_type="Two-Wheeler")
        ev = database.get_available_slots(self.db, slot_type="EV")
        self.assertEqual(len(standard), 20)
        self.assertEqual(len(compact), 15)
        self.assertEqual(len(two_wheeler), 10)
        self.assertEqual(len(ev), 5)


# ===========================================================================
# 2. Valid Vehicle Registration
# ===========================================================================

class TestValidVehicleRegistration(_TempDBMixin):
    """Verify that well-formed registration attempts create a complete session record."""

    def test_basic_entry_returns_active_session(self):
        """A valid entry must return session_id, slot info, and ACTIVE status."""
        result = self._park("MH12AB1234", entry_time="2026-06-15 09:00:00")
        self.assertIsInstance(result["session_id"], int)
        self.assertEqual(result["vehicle_registration_number"], "MH12AB1234")
        self.assertEqual(result["status"], "ACTIVE")
        self.assertIsNotNone(result["slot_id"])
        self.assertIsNotNone(result["slot_number"])

    def test_whitespace_and_case_normalization(self):
        """Registration numbers are uppercased and stripped."""
        result = self._park(" dl-01-XY-9999 ", entry_time="2026-06-15 09:00:00")
        self.assertEqual(result["vehicle_registration_number"], "DL-01-XY-9999")

    def test_owner_name_stripped(self):
        """Owner name whitespace is stripped before storage."""
        result = self._park("KA05ZZ1234", owner_name="  Alice Smith  ",
                            entry_time="2026-06-15 09:00:00")
        self.assertEqual(result["owner_name"], "Alice Smith")

    def test_session_persisted_in_database(self):
        """The session row must be retrievable by ID after registration."""
        result = self._park("GJ01BB2345", entry_time="2026-06-15 10:00:00")
        row = database.get_session_by_id(result["session_id"], db_path=self.db)
        self.assertIsNotNone(row)
        self.assertEqual(row["vehicle_registration_number"], "GJ01BB2345")
        self.assertEqual(row["entry_time"], "2026-06-15 10:00:00")
        self.assertEqual(row["status"], "ACTIVE")

    def test_slot_marked_occupied_after_entry(self):
        """The assigned slot must transition to 'Occupied' after vehicle entry."""
        result = self._park("HR26CC4567", entry_time="2026-06-15 08:00:00")
        slot = database.get_slot_by_id(result["slot_id"], db_path=self.db)
        self.assertEqual(slot["status"], "Occupied")

    def test_multiple_distinct_vehicles_can_enter(self):
        """Different vehicles must be allowed to enter simultaneously."""
        r1 = self._park("AA01AA0001", entry_time="2026-06-15 08:00:00")
        r2 = self._park("AA02BB0002", entry_time="2026-06-15 08:05:00")
        r3 = self._park("AA03CC0003", entry_time="2026-06-15 08:10:00")
        self.assertNotEqual(r1["slot_id"], r2["slot_id"])
        self.assertNotEqual(r2["slot_id"], r3["slot_id"])
        self.assertEqual(len(database.get_all_active_sessions(self.db)), 3)

    def test_vehicle_types_accepted(self):
        """All supported vehicle types must register without error."""
        plates_types = [
            ("TW01AA1001", "Two-Wheeler"),
            ("EV01BB2002", "EV"),
            ("CP01CC3003", "Compact"),
            ("HV01DD4004", "Heavy"),
            ("CR01EE5005", "Car"),
        ]
        for plate, vtype in plates_types:
            with self.subTest(vehicle_type=vtype):
                r = self._park(plate, vehicle_type=vtype,
                               entry_time="2026-06-15 09:00:00")
                self.assertEqual(r["vehicle_type"], vtype)


# ===========================================================================
# 3. Invalid Required Fields
# ===========================================================================

class TestInvalidRequiredFields(_TempDBMixin):
    """Verify that malformed inputs are rejected with ValidationError."""

    def _assert_validation_error(self, plate):
        with self.assertRaises(parking_service.ValidationError,
                               msg=f"Expected ValidationError for plate={plate!r}"):
            parking_service.register_vehicle_entry(plate, db_path=self.db)

    def test_empty_string_plate_rejected(self):
        self._assert_validation_error("")

    def test_whitespace_only_plate_rejected(self):
        self._assert_validation_error("   ")

    def test_too_short_plate_rejected(self):
        self._assert_validation_error("AB")

    def test_too_long_plate_rejected(self):
        self._assert_validation_error("A" * 17)

    def test_special_characters_rejected(self):
        self._assert_validation_error("MH-12@AB-1234")

    def test_fewer_than_3_alphanumeric_rejected(self):
        """A plate that is only hyphens/spaces must be rejected."""
        self._assert_validation_error("--  ")

    def test_none_plate_rejected(self):
        with self.assertRaises((parking_service.ValidationError, TypeError, AttributeError)):
            parking_service.register_vehicle_entry(None, db_path=self.db)

    def test_invalid_vehicle_type_rejected(self):
        with self.assertRaises(parking_service.ValidationError):
            parking_service.register_vehicle_entry(
                "MH12AB1234", vehicle_type="Hovercraft", db_path=self.db
            )

    def test_invalid_timestamp_rejected(self):
        with self.assertRaises(parking_service.ValidationError):
            parking_service.register_vehicle_entry(
                "MH12AB1234",
                entry_time="not-a-timestamp",
                db_path=self.db,
            )

    def test_discount_must_be_numeric(self):
        """A non-numeric discount_amount must raise ValidationError."""
        entry = self._park("DL04AA9999", entry_time="2026-06-15 08:00:00")
        with self.assertRaises(parking_service.ValidationError):
            parking_service.record_vehicle_exit(
                session_id=entry["session_id"],
                exit_time="2026-06-15 10:00:00",
                discount_amount="ten",   # type: ignore[arg-type]
                db_path=self.db,
            )

    def test_negative_discount_rejected(self):
        entry = self._park("KA03AB1111", entry_time="2026-06-15 08:00:00")
        with self.assertRaises(parking_service.ValidationError):
            self._exit(session_id=entry["session_id"],
                       exit_time="2026-06-15 10:00:00", discount=-5.0)

    def test_exit_requires_session_id_or_plate(self):
        """record_vehicle_exit with neither session_id nor plate must raise ValidationError."""
        with self.assertRaises(parking_service.ValidationError):
            parking_service.record_vehicle_exit(db_path=self.db)

    def test_update_session_not_found_raises(self):
        with self.assertRaises(parking_service.SessionNotFoundError):
            parking_service.update_permitted_vehicle_info(
                session_id=99999, owner_name="Ghost", db_path=self.db
            )


# ===========================================================================
# 4. Duplicate Active Vehicle Prevention
# ===========================================================================

class TestDuplicateActiveVehiclePrevention(_TempDBMixin):
    """Verify that a vehicle cannot have two simultaneous ACTIVE sessions."""

    def test_same_plate_same_case_rejected(self):
        self._park("KL10XX1234", entry_time="2026-06-15 08:00:00")
        with self.assertRaises(parking_service.DuplicateActiveSessionError):
            self._park("KL10XX1234", entry_time="2026-06-15 09:00:00")

    def test_same_plate_different_case_rejected(self):
        """Duplicate check is case-insensitive."""
        self._park("DL01AB1234", entry_time="2026-06-15 08:00:00")
        with self.assertRaises(parking_service.DuplicateActiveSessionError):
            self._park("dl01ab1234", entry_time="2026-06-15 09:00:00")

    def test_duplicate_rejected_with_padded_whitespace(self):
        """Whitespace-padded duplicates must still be caught."""
        self._park("HR26CC3456", entry_time="2026-06-15 08:00:00")
        with self.assertRaises(parking_service.DuplicateActiveSessionError):
            self._park("  hr26cc3456  ", entry_time="2026-06-15 09:00:00")

    def test_only_one_active_session_exists_after_rejection(self):
        """The database must contain exactly one active session for the vehicle."""
        self._park("WB02YY7890", entry_time="2026-06-15 08:00:00")
        try:
            self._park("WB02YY7890", entry_time="2026-06-15 09:00:00")
        except parking_service.DuplicateActiveSessionError:
            pass
        active = database.get_all_active_sessions(self.db)
        vehicle_sessions = [s for s in active
                            if s["vehicle_registration_number"] == "WB02YY7890"]
        self.assertEqual(len(vehicle_sessions), 1)

    def test_reentry_allowed_after_completed_exit(self):
        """Vehicle may park again after its previous session is fully exited."""
        entry1 = self._park("GJ05AA0001", entry_time="2026-06-15 08:00:00")
        self._exit(session_id=entry1["session_id"], exit_time="2026-06-15 09:00:00")
        # Should succeed -- no active session anymore
        entry2 = self._park("GJ05AA0001", entry_time="2026-06-15 10:00:00")
        self.assertNotEqual(entry1["session_id"], entry2["session_id"])
        self.assertEqual(entry2["status"], "ACTIVE")

    def test_db_layer_duplicate_prevention(self):
        """The database layer's start_parking_session also raises ActiveSessionExistsError."""
        slot = self._first_available_slot()
        database.start_parking_session("TN07BB9999", slot["id"],
                                       db_path=self.db)
        slot2 = database.get_available_slots(self.db)[0]
        with self.assertRaises(database.ActiveSessionExistsError):
            database.start_parking_session("TN07BB9999", slot2["id"],
                                           db_path=self.db)


# ===========================================================================
# 5. Parking Capacity Enforcement
# ===========================================================================

class TestParkingCapacityEnforcement(_TempDBMixin):
    """Verify that a full facility refuses new vehicles."""

    def test_capacity_full_error_when_all_slots_occupied(self):
        """Filling every slot and then attempting another entry raises ParkingCapacityFullError."""
        self._fill_all_slots()
        with self.assertRaises(parking_service.ParkingCapacityFullError):
            parking_service.register_vehicle_entry(
                "OVERFLOW001", entry_time="2026-01-01 09:00:00", db_path=self.db
            )

    def test_capacity_full_after_all_slots_set_maintenance(self):
        """Maintenance slots are not available; a full maintenance lot blocks entry."""
        all_slots = database.get_all_slots(self.db)
        # Put all but the first slot in Maintenance
        for slot in all_slots[1:]:
            database.update_slot_status(slot["id"], "Maintenance", self.db)
        self._park("FIRST001", slot_id=all_slots[0]["id"],
                   entry_time="2026-01-01 08:00:00")
        with self.assertRaises(parking_service.ParkingCapacityFullError):
            self._park("SECOND001", entry_time="2026-01-01 08:05:00")

    def test_slot_available_after_exit_allows_reentry(self):
        """After exiting the last vehicle, the facility can accept new arrivals."""
        self._lock_all_slots_except_first()
        entry = self._park("SOLO001", entry_time="2026-01-01 08:00:00")
        # Now full
        with self.assertRaises(parking_service.ParkingCapacityFullError):
            self._park("FULL001", entry_time="2026-01-01 08:01:00")
        # Exit the solo vehicle -- slot is now free
        self._exit(session_id=entry["session_id"], exit_time="2026-01-01 10:00:00")
        new_entry = self._park("AFTER001", entry_time="2026-01-01 10:05:00")
        self.assertEqual(new_entry["status"], "ACTIVE")

    def test_capacity_metrics_reflect_real_state(self):
        """get_parking_capacity_status must return accurate counts."""
        initial = parking_service.get_parking_capacity_status(self.db)
        self.assertEqual(initial["total_slots"], 50)
        self.assertEqual(initial["available_slots"], 50)
        self.assertEqual(initial["occupied_slots"], 0)

        entry = self._park("TN99ZZ9999", entry_time="2026-01-01 08:00:00")

        after = parking_service.get_parking_capacity_status(self.db)
        self.assertEqual(after["available_slots"], 49)
        self.assertEqual(after["occupied_slots"], 1)

        self._exit(session_id=entry["session_id"], exit_time="2026-01-01 10:00:00")

        final = parking_service.get_parking_capacity_status(self.db)
        self.assertEqual(final["available_slots"], 50)
        self.assertEqual(final["occupied_slots"], 0)


# ===========================================================================
# 6. Parking Slot Assignment
# ===========================================================================

class TestParkingSlotAssignment(_TempDBMixin):
    """Verify automatic slot allocation preferences and explicit slot selection."""

    def test_two_wheeler_gets_two_wheeler_slot_first(self):
        result = self._park("BIKE001", vehicle_type="Two-Wheeler",
                            entry_time="2026-06-15 08:00:00")
        slot = database.get_slot_by_id(result["slot_id"], self.db)
        self.assertEqual(slot["slot_type"], "Two-Wheeler")

    def test_ev_gets_ev_slot_first(self):
        result = self._park("ELEC001", vehicle_type="EV",
                            entry_time="2026-06-15 08:00:00")
        slot = database.get_slot_by_id(result["slot_id"], self.db)
        self.assertEqual(slot["slot_type"], "EV")

    def test_car_gets_standard_slot_first(self):
        result = self._park("CAR001", vehicle_type="Car",
                            entry_time="2026-06-15 08:00:00")
        slot = database.get_slot_by_id(result["slot_id"], self.db)
        self.assertEqual(slot["slot_type"], "Standard")

    def test_heavy_falls_back_to_standard_when_heavy_slots_absent(self):
        """If no Heavy slots exist, a Standard slot is used."""
        result = self._park("TRUCK001", vehicle_type="Heavy",
                            entry_time="2026-06-15 08:00:00")
        slot = database.get_slot_by_id(result["slot_id"], self.db)
        self.assertIn(slot["slot_type"], ("Heavy", "Standard"))

    def test_explicit_slot_by_id(self):
        slots = database.get_available_slots(self.db)
        chosen = slots[5]
        result = self._park("EXPLICIT001", slot_id=chosen["id"],
                            entry_time="2026-06-15 08:00:00")
        self.assertEqual(result["slot_id"], chosen["id"])
        self.assertEqual(result["slot_number"], chosen["slot_number"])

    def test_explicit_slot_by_number(self):
        result = self._park("SLOTNO001", slot_number="A-10",
                            entry_time="2026-06-15 08:00:00")
        self.assertEqual(result["slot_number"], "A-10")

    def test_occupied_slot_cannot_be_explicitly_assigned(self):
        slot = self._first_available_slot()
        self._park("FIRST001", slot_id=slot["id"],
                   entry_time="2026-06-15 08:00:00")
        with self.assertRaises(parking_service.SlotNotAvailableError):
            self._park("SECOND001", slot_id=slot["id"],
                       entry_time="2026-06-15 08:05:00")

    def test_nonexistent_slot_id_raises(self):
        with self.assertRaises(parking_service.SlotNotFoundError):
            self._park("NOSLT001", slot_id=999999,
                       entry_time="2026-06-15 08:00:00")

    def test_nonexistent_slot_number_raises(self):
        with self.assertRaises(parking_service.SlotNotFoundError):
            self._park("NOSLT002", slot_number="Z-99",
                       entry_time="2026-06-15 08:00:00")

    def test_two_vehicles_cannot_share_the_same_slot(self):
        """Even via database layer, slot uniqueness index must block it."""
        slot = self._first_available_slot()
        database.start_parking_session("DBCAR001", slot["id"], db_path=self.db)
        with self.assertRaises(database.SlotNotAvailableError):
            database.start_parking_session("DBCAR002", slot["id"], db_path=self.db)

    def test_slot_status_becomes_occupied(self):
        slot = self._first_available_slot()
        self._park("OCCUP001", slot_id=slot["id"],
                   entry_time="2026-06-15 08:00:00")
        updated = database.get_slot_by_id(slot["id"], self.db)
        self.assertEqual(updated["status"], "Occupied")


# ===========================================================================
# 7. Vehicle Search
# ===========================================================================

class TestVehicleSearch(_TempDBMixin):
    """Verify search_parking_records filters work correctly."""

    def setUp(self):
        super().setUp()
        self.e1 = self._park("MH12AB1234", owner_name="Alice",
                             vehicle_type="Car", entry_time="2026-06-15 08:00:00")
        self.e2 = self._park("DL01XY9999", owner_name="Bob",
                             vehicle_type="Two-Wheeler", entry_time="2026-06-15 09:00:00")
        # Complete session 1
        self._exit(session_id=self.e1["session_id"], exit_time="2026-06-15 10:00:00")

    def test_search_by_plate_keyword(self):
        results = parking_service.search_parking_records(keyword="MH12", db_path=self.db)
        ids = [r["id"] for r in results]
        self.assertIn(self.e1["session_id"], ids)
        self.assertNotIn(self.e2["session_id"], ids)

    def test_search_by_owner_name_keyword(self):
        results = parking_service.search_parking_records(keyword="alice", db_path=self.db)
        ids = [r["id"] for r in results]
        self.assertIn(self.e1["session_id"], ids)

    def test_search_by_status_active(self):
        results = parking_service.search_parking_records(status="ACTIVE", db_path=self.db)
        statuses = {r["status"] for r in results}
        self.assertEqual(statuses, {"ACTIVE"})

    def test_search_by_status_completed(self):
        results = parking_service.search_parking_records(status="COMPLETED", db_path=self.db)
        statuses = {r["status"] for r in results}
        self.assertEqual(statuses, {"COMPLETED"})

    def test_search_by_vehicle_type(self):
        results = parking_service.search_parking_records(
            vehicle_type="Two-Wheeler", db_path=self.db
        )
        for r in results:
            self.assertEqual(r["vehicle_type"], "Two-Wheeler")

    def test_search_all_returns_both_statuses(self):
        results = parking_service.search_parking_records(db_path=self.db)
        statuses = {r["status"] for r in results}
        self.assertIn("ACTIVE", statuses)
        self.assertIn("COMPLETED", statuses)

    def test_search_with_no_match_returns_empty(self):
        results = parking_service.search_parking_records(
            keyword="XXXXNOSUCHVEHICLE", db_path=self.db
        )
        self.assertEqual(results, [])

    def test_invalid_status_filter_raises_validation_error(self):
        with self.assertRaises(parking_service.ValidationError):
            parking_service.search_parking_records(status="UNKNOWN", db_path=self.db)

    def test_get_vehicle_history_returns_all_sessions(self):
        """get_vehicle_history must return all records for a plate."""
        history = parking_service.get_vehicle_history("MH12AB1234", db_path=self.db)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["status"], "COMPLETED")

    def test_get_vehicle_history_case_insensitive(self):
        history_lower = parking_service.get_vehicle_history("mh12ab1234", db_path=self.db)
        history_upper = parking_service.get_vehicle_history("MH12AB1234", db_path=self.db)
        self.assertEqual(len(history_lower), len(history_upper))

    def test_current_stay_minutes_set_for_active_sessions(self):
        """Active session results must include a non-negative current_stay_minutes."""
        results = parking_service.search_parking_records(status="ACTIVE", db_path=self.db)
        for r in results:
            with self.subTest(session_id=r["id"]):
                self.assertIn("current_stay_minutes", r)
                self.assertGreaterEqual(r["current_stay_minutes"], 0)


# ===========================================================================
# 8. Permitted Record Updates
# ===========================================================================

class TestPermittedRecordUpdates(_TempDBMixin):
    """Verify that allowed fields can be updated and immutable fields cannot."""

    def setUp(self):
        super().setUp()
        self.entry = self._park("KA05ZZ1234", owner_name="Original Name",
                                vehicle_type="Car", entry_time="2026-06-15 08:00:00")
        self.sid = self.entry["session_id"]

    def test_update_owner_name(self):
        updated = parking_service.update_permitted_vehicle_info(
            self.sid, owner_name="Updated Name", db_path=self.db
        )
        self.assertEqual(updated["owner_name"], "Updated Name")

    def test_update_owner_contact(self):
        updated = parking_service.update_permitted_vehicle_info(
            self.sid, owner_contact="555-1234", db_path=self.db
        )
        self.assertEqual(updated["owner_contact"], "555-1234")

    def test_update_vehicle_type(self):
        updated = parking_service.update_permitted_vehicle_info(
            self.sid, vehicle_type="EV", db_path=self.db
        )
        self.assertEqual(updated["vehicle_type"], "EV")

    def test_update_multiple_fields_simultaneously(self):
        updated = parking_service.update_permitted_vehicle_info(
            self.sid,
            owner_name="New Owner",
            owner_contact="999-0000",
            vehicle_type="Compact",
            db_path=self.db,
        )
        self.assertEqual(updated["owner_name"], "New Owner")
        self.assertEqual(updated["owner_contact"], "999-0000")
        self.assertEqual(updated["vehicle_type"], "Compact")

    def test_update_does_not_change_slot_or_entry_time(self):
        """Immutable fields must remain unchanged after an update."""
        original_slot_id = self.entry["slot_id"]
        original_entry = self.entry["entry_time"]
        updated = parking_service.update_permitted_vehicle_info(
            self.sid, owner_name="Changed", db_path=self.db
        )
        self.assertEqual(updated["slot_id"], original_slot_id)
        self.assertEqual(updated["entry_time"], original_entry)

    def test_update_nonexistent_session_raises(self):
        with self.assertRaises(parking_service.SessionNotFoundError):
            parking_service.update_permitted_vehicle_info(
                session_id=99999, owner_name="Ghost", db_path=self.db
            )

    def test_update_with_no_fields_is_safe_no_op(self):
        """Calling update with no fields specified must not raise or corrupt data."""
        parking_service.update_permitted_vehicle_info(self.sid, db_path=self.db)
        row = database.get_session_by_id(self.sid, self.db)
        self.assertEqual(row["owner_name"], "Original Name")

    def test_database_layer_update_fields(self):
        """database.update_session_details must also update correctly."""
        database.update_session_details(
            session_id=self.sid,
            owner_name="DB Updated",
            owner_contact="800-0000",
            db_path=self.db,
        )
        row = database.get_session_by_id(self.sid, self.db)
        self.assertEqual(row["owner_name"], "DB Updated")
        self.assertEqual(row["owner_contact"], "800-0000")


# ===========================================================================
# 9. Safe Deletion Rules
# ===========================================================================

class TestSafeDeletionRules(_TempDBMixin):
    """Verify business-rule protection of parking records before deletion."""

    def test_cannot_delete_active_session(self):
        entry = self._park("DEL001", entry_time="2026-06-15 08:00:00")
        with self.assertRaises(parking_service.UnsafeDeletionError):
            parking_service.delete_parking_record_safely(
                entry["session_id"], db_path=self.db
            )

    def test_cannot_delete_billed_session_without_override(self):
        entry = self._park("DEL002", entry_time="2026-06-15 08:00:00")
        self._exit(session_id=entry["session_id"], exit_time="2026-06-15 10:00:00")
        # Fee > 0 so override required
        with self.assertRaises(parking_service.UnsafeDeletionError):
            parking_service.delete_parking_record_safely(
                entry["session_id"], admin_override=False, db_path=self.db
            )

    def test_cannot_delete_billed_session_without_audit_reason(self):
        entry = self._park("DEL003", entry_time="2026-06-15 08:00:00")
        self._exit(session_id=entry["session_id"], exit_time="2026-06-15 10:00:00")
        for bad_reason in ("", "   "):
            with self.subTest(reason=bad_reason):
                with self.assertRaises(parking_service.UnsafeDeletionError):
                    parking_service.delete_parking_record_safely(
                        entry["session_id"],
                        admin_override=True,
                        audit_reason=bad_reason,
                        db_path=self.db,
                    )

    def test_admin_override_with_valid_reason_deletes_billed_session(self):
        entry = self._park("DEL004", entry_time="2026-06-15 08:00:00")
        self._exit(session_id=entry["session_id"], exit_time="2026-06-15 10:00:00")
        parking_service.delete_parking_record_safely(
            entry["session_id"],
            admin_override=True,
            audit_reason="GDPR erasure request #789",
            db_path=self.db,
        )
        self.assertIsNone(database.get_session_by_id(entry["session_id"], self.db))

    def test_can_delete_cancelled_session_without_override(self):
        """A cancelled (zero-fee) session can be deleted without admin override."""
        entry = self._park("DEL005", entry_time="2026-06-15 08:00:00")
        parking_service.cancel_active_session_safely(
            entry["session_id"], db_path=self.db
        )
        parking_service.delete_parking_record_safely(
            entry["session_id"], db_path=self.db
        )
        self.assertIsNone(database.get_session_by_id(entry["session_id"], self.db))

    def test_delete_nonexistent_session_raises(self):
        with self.assertRaises(parking_service.SessionNotFoundError):
            parking_service.delete_parking_record_safely(
                88888, db_path=self.db
            )

    def test_cancelling_non_active_session_raises(self):
        """cancel_active_session_safely must fail for already-completed sessions."""
        entry = self._park("DEL006", entry_time="2026-06-15 08:00:00")
        self._exit(session_id=entry["session_id"], exit_time="2026-06-15 10:00:00")
        with self.assertRaises(parking_service.InvalidExitError):
            parking_service.cancel_active_session_safely(
                entry["session_id"], db_path=self.db
            )

    def test_can_delete_zero_fee_completed_session(self):
        """A completed session with fee=0 (grace period exit) needs no override."""
        entry = self._park("DEL007", entry_time="2026-06-15 08:00:00")
        # Exit within 5 minutes -- within grace period, fee = $0
        self._exit(session_id=entry["session_id"], exit_time="2026-06-15 08:05:00")
        row = database.get_session_by_id(entry["session_id"], self.db)
        self.assertEqual(row["fee"], 0.0)
        # No override needed for zero-fee completed sessions
        parking_service.delete_parking_record_safely(
            entry["session_id"], db_path=self.db
        )
        self.assertIsNone(database.get_session_by_id(entry["session_id"], self.db))


# ===========================================================================
# 10. Parking Duration and Fee Calculation
# ===========================================================================

class TestDurationAndFeeCalculation(_TempDBMixin):
    """Verify the approved fee policy using fixed timestamps."""

    def _fee(self, entry, exit_, vtype="Car"):
        return parking_service.calculate_duration_and_fee(
            entry, exit_, vehicle_type=vtype, db_path=self.db
        )

    def test_within_grace_period_zero_fee(self):
        """Stay of exactly 15 minutes is free."""
        result = self._fee("2026-06-15 08:00:00", "2026-06-15 08:15:00")
        self.assertEqual(result["fee"], 0.0)
        self.assertEqual(result["billable_hours"], 0)

    def test_under_grace_period_zero_fee(self):
        """Stay of 5 minutes is free."""
        result = self._fee("2026-06-15 08:00:00", "2026-06-15 08:05:00")
        self.assertEqual(result["fee"], 0.0)

    def test_one_minute_over_grace_bills_minimum_one_hour(self):
        """16-minute stay triggers minimum 1 billable hour."""
        result = self._fee("2026-06-15 08:00:00", "2026-06-15 08:16:00")
        self.assertEqual(result["billable_hours"], 1)

    def test_exactly_one_hour_bills_one_hour(self):
        result = self._fee("2026-06-15 08:00:00", "2026-06-15 09:00:00")
        self.assertEqual(result["billable_hours"], 1)

    def test_one_hour_one_minute_bills_two_hours(self):
        """Ceil rounding: 1 hr 1 min -> 2 billable hours."""
        result = self._fee("2026-06-15 08:00:00", "2026-06-15 09:01:00")
        self.assertEqual(result["billable_hours"], 2)

    def test_car_rate_20_per_hour(self):
        """Default car rate is $20.00/hr."""
        result = self._fee("2026-06-15 08:00:00", "2026-06-15 10:00:00", "Car")
        self.assertEqual(result["hourly_rate"], 20.0)
        self.assertEqual(result["fee"], 40.0)

    def test_two_wheeler_rate_10_per_hour(self):
        result = self._fee("2026-06-15 08:00:00", "2026-06-15 10:00:00", "Two-Wheeler")
        self.assertEqual(result["hourly_rate"], 10.0)
        self.assertEqual(result["fee"], 20.0)

    def test_heavy_rate_40_per_hour(self):
        result = self._fee("2026-06-15 08:00:00", "2026-06-15 10:00:00", "Heavy")
        self.assertEqual(result["hourly_rate"], 40.0)
        self.assertEqual(result["fee"], 80.0)

    def test_ev_uses_car_rate(self):
        """EV is billed at the car rate."""
        result = self._fee("2026-06-15 08:00:00", "2026-06-15 10:00:00", "EV")
        self.assertEqual(result["hourly_rate"], 20.0)

    def test_compact_uses_car_rate(self):
        result = self._fee("2026-06-15 08:00:00", "2026-06-15 10:00:00", "Compact")
        self.assertEqual(result["hourly_rate"], 20.0)

    def test_exit_before_entry_raises(self):
        with self.assertRaises(parking_service.InvalidExitError):
            self._fee("2026-06-15 10:00:00", "2026-06-15 08:00:00")

    def test_duration_formatted_string(self):
        result = self._fee("2026-06-15 08:00:00", "2026-06-15 10:30:00")
        self.assertEqual(result["duration_formatted"], "2h 30m")

    def test_dynamic_rate_change_reflected(self):
        """Updating rate_car in settings changes future fee calculations."""
        database.set_setting("rate_car", "30.0", db_path=self.db)
        result = self._fee("2026-06-15 08:00:00", "2026-06-15 10:00:00", "Car")
        self.assertEqual(result["hourly_rate"], 30.0)
        self.assertEqual(result["fee"], 60.0)

    def test_grace_period_change_reflected(self):
        """Changing grace_period_mins in settings changes threshold."""
        database.set_setting("grace_period_mins", "5", db_path=self.db)
        # 10 minute stay: beyond new 5-min grace -> should be billed
        result = self._fee("2026-06-15 08:00:00", "2026-06-15 08:10:00")
        self.assertEqual(result["billable_hours"], 1)
        self.assertGreater(result["fee"], 0)

    def test_db_layer_calculate_fee_grace_period(self):
        """database.calculate_fee must also respect the grace period."""
        fee = database.calculate_fee(
            "2026-06-15 08:00:00", "2026-06-15 08:10:00",
            vehicle_type="Car", db_path=self.db
        )
        self.assertEqual(fee, 0.0)

    def test_db_layer_calculate_fee_two_hours(self):
        fee = database.calculate_fee(
            "2026-06-15 08:00:00", "2026-06-15 10:00:00",
            vehicle_type="Car", db_path=self.db
        )
        self.assertEqual(fee, 40.0)


# ===========================================================================
# 11. Vehicle Exit
# ===========================================================================

class TestVehicleExit(_TempDBMixin):
    """Verify that record_vehicle_exit correctly closes a session and records the fee."""

    def test_exit_by_session_id(self):
        entry = self._park("EXIT001", entry_time="2026-06-15 08:00:00")
        receipt = self._exit(session_id=entry["session_id"],
                             exit_time="2026-06-15 10:00:00")
        self.assertEqual(receipt["status"], "COMPLETED")
        self.assertEqual(receipt["exit_time"], "2026-06-15 10:00:00")
        self.assertGreater(receipt["final_fee"], 0)

    def test_exit_by_plate_number(self):
        entry = self._park("EXIT002", entry_time="2026-06-15 08:00:00")
        receipt = parking_service.record_vehicle_exit(
            vehicle_registration_number="EXIT002",
            exit_time="2026-06-15 10:00:00",
            db_path=self.db,
        )
        self.assertEqual(receipt["status"], "COMPLETED")
        self.assertEqual(receipt["vehicle_registration_number"], "EXIT002")

    def test_exit_plate_is_case_insensitive(self):
        entry = self._park("EXIT003", entry_time="2026-06-15 08:00:00")
        receipt = parking_service.record_vehicle_exit(
            vehicle_registration_number="exit003",
            exit_time="2026-06-15 10:00:00",
            db_path=self.db,
        )
        self.assertEqual(receipt["status"], "COMPLETED")

    def test_exit_before_entry_raises(self):
        """Exit timestamp before entry must raise InvalidExitError before DB write."""
        entry = self._park("EXIT004", entry_time="2026-06-15 10:00:00")
        with self.assertRaises(parking_service.InvalidExitError):
            self._exit(session_id=entry["session_id"],
                       exit_time="2026-06-15 08:00:00")
        # Session must still be ACTIVE
        row = database.get_session_by_id(entry["session_id"], self.db)
        self.assertEqual(row["status"], "ACTIVE")

    def test_exit_with_discount(self):
        entry = self._park("EXIT005", vehicle_type="Car",
                           entry_time="2026-06-15 08:00:00")
        receipt = self._exit(session_id=entry["session_id"],
                             exit_time="2026-06-15 10:00:00", discount=10.0)
        self.assertEqual(receipt["base_fee"], 40.0)
        self.assertEqual(receipt["discount_applied"], 10.0)
        self.assertEqual(receipt["final_fee"], 30.0)

    def test_discount_exceeding_fee_rejected(self):
        entry = self._park("EXIT006", vehicle_type="Car",
                           entry_time="2026-06-15 08:00:00")
        with self.assertRaises(parking_service.ValidationError):
            self._exit(session_id=entry["session_id"],
                       exit_time="2026-06-15 10:00:00",
                       discount=999.0)  # 999 > 40.0

    def test_exit_session_marked_completed_in_db(self):
        entry = self._park("EXIT007", entry_time="2026-06-15 08:00:00")
        self._exit(session_id=entry["session_id"], exit_time="2026-06-15 10:00:00")
        row = database.get_session_by_id(entry["session_id"], self.db)
        self.assertEqual(row["status"], "COMPLETED")
        self.assertEqual(row["exit_time"], "2026-06-15 10:00:00")
        self.assertIsNotNone(row["fee"])

    def test_exit_nonexistent_session_raises(self):
        with self.assertRaises(parking_service.SessionNotFoundError):
            self._exit(session_id=99999, exit_time="2026-06-15 10:00:00")

    def test_exit_already_completed_session_raises(self):
        entry = self._park("EXIT008", entry_time="2026-06-15 08:00:00")
        self._exit(session_id=entry["session_id"], exit_time="2026-06-15 10:00:00")
        with self.assertRaises(parking_service.InvalidExitError):
            self._exit(session_id=entry["session_id"], exit_time="2026-06-15 11:00:00")

    def test_exit_nonexistent_plate_raises(self):
        with self.assertRaises(parking_service.SessionNotFoundError):
            parking_service.record_vehicle_exit(
                vehicle_registration_number="NOSUCHCAR",
                exit_time="2026-06-15 10:00:00",
                db_path=self.db,
            )

    def test_receipt_contains_all_expected_fields(self):
        entry = self._park("EXIT009", vehicle_type="Car",
                           owner_name="Tester", entry_time="2026-06-15 08:00:00")
        receipt = self._exit(session_id=entry["session_id"],
                             exit_time="2026-06-15 10:00:00")
        for field in ("session_id", "vehicle_registration_number", "vehicle_type",
                      "slot_id", "slot_number", "entry_time", "exit_time",
                      "duration_minutes", "duration_formatted", "billable_hours",
                      "hourly_rate", "base_fee", "discount_applied", "final_fee",
                      "status"):
            with self.subTest(field=field):
                self.assertIn(field, receipt)


# ===========================================================================
# 12. Slot Release
# ===========================================================================

class TestSlotRelease(_TempDBMixin):
    """Verify that slots revert to Available after exit or cancellation."""

    def test_slot_released_to_available_after_normal_exit(self):
        slot = self._first_available_slot()
        entry = self._park("SREL001", slot_id=slot["id"],
                           entry_time="2026-06-15 08:00:00")
        self.assertEqual(database.get_slot_by_id(slot["id"], self.db)["status"],
                         "Occupied")
        self._exit(session_id=entry["session_id"], exit_time="2026-06-15 10:00:00")
        self.assertEqual(database.get_slot_by_id(slot["id"], self.db)["status"],
                         "Available")

    def test_slot_released_after_cancellation(self):
        slot = self._first_available_slot()
        entry = self._park("SREL002", slot_id=slot["id"],
                           entry_time="2026-06-15 08:00:00")
        parking_service.cancel_active_session_safely(
            entry["session_id"], db_path=self.db
        )
        self.assertEqual(database.get_slot_by_id(slot["id"], self.db)["status"],
                         "Available")

    def test_slot_not_released_if_exit_fails(self):
        """If exit timestamp is invalid, the slot must remain Occupied."""
        slot = self._first_available_slot()
        entry = self._park("SREL003", slot_id=slot["id"],
                           entry_time="2026-06-15 10:00:00")
        try:
            self._exit(session_id=entry["session_id"],
                       exit_time="2026-06-15 08:00:00")  # before entry
        except parking_service.InvalidExitError:
            pass
        self.assertEqual(database.get_slot_by_id(slot["id"], self.db)["status"],
                         "Occupied")

    def test_released_slot_can_be_reused(self):
        slot = self._first_available_slot()
        e1 = self._park("SREL004", slot_id=slot["id"],
                        entry_time="2026-06-15 08:00:00")
        self._exit(session_id=e1["session_id"], exit_time="2026-06-15 09:00:00")
        e2 = self._park("SREL005", slot_id=slot["id"],
                        entry_time="2026-06-15 10:00:00")
        self.assertEqual(e2["slot_id"], slot["id"])
        self.assertEqual(database.get_slot_by_id(slot["id"], self.db)["status"],
                         "Occupied")

    def test_db_layer_slot_available_after_cancel(self):
        """database.cancel_parking_session must also release the slot."""
        slot = self._first_available_slot()
        sid = database.start_parking_session("DBREL001", slot["id"],
                                             db_path=self.db)
        self.assertEqual(database.get_slot_by_id(slot["id"], self.db)["status"],
                         "Occupied")
        database.cancel_parking_session(sid, db_path=self.db)
        self.assertEqual(database.get_slot_by_id(slot["id"], self.db)["status"],
                         "Available")


# ===========================================================================
# 13. Historical Record Preservation
# ===========================================================================

class TestHistoricalRecordPreservation(_TempDBMixin):
    """Verify that completed sessions are permanently preserved and queryable."""

    def test_completed_session_is_not_deleted_on_exit(self):
        entry = self._park("HIST001", entry_time="2026-06-15 08:00:00")
        self._exit(session_id=entry["session_id"], exit_time="2026-06-15 10:00:00")
        row = database.get_session_by_id(entry["session_id"], self.db)
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "COMPLETED")

    def test_multiple_visits_all_preserved(self):
        """A vehicle's full parking history across multiple visits must persist."""
        slot = self._first_available_slot()
        for i in range(3):
            entry = self._park("HIST002", slot_id=slot["id"],
                               entry_time=f"2026-06-{15+i} 08:00:00")
            self._exit(session_id=entry["session_id"],
                       exit_time=f"2026-06-{15+i} 10:00:00")

        history = parking_service.get_vehicle_history("HIST002", db_path=self.db)
        self.assertEqual(len(history), 3)
        for row in history:
            self.assertEqual(row["status"], "COMPLETED")

    def test_get_completed_sessions_returns_only_completed(self):
        entry1 = self._park("HIST003", entry_time="2026-06-15 08:00:00")
        entry2 = self._park("HIST004", entry_time="2026-06-15 08:05:00")
        self._exit(session_id=entry1["session_id"], exit_time="2026-06-15 10:00:00")
        # entry2 remains ACTIVE
        completed = database.get_completed_sessions(self.db)
        ids = [r["id"] for r in completed]
        self.assertIn(entry1["session_id"], ids)
        self.assertNotIn(entry2["session_id"], ids)

    def test_cancelled_session_also_preserved(self):
        entry = self._park("HIST005", entry_time="2026-06-15 08:00:00")
        parking_service.cancel_active_session_safely(
            entry["session_id"], db_path=self.db
        )
        row = database.get_session_by_id(entry["session_id"], self.db)
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "CANCELLED")

    def test_fee_preserved_on_completed_session(self):
        entry = self._park("HIST006", vehicle_type="Car",
                           entry_time="2026-06-15 08:00:00")
        self._exit(session_id=entry["session_id"], exit_time="2026-06-15 10:00:00")
        row = database.get_session_by_id(entry["session_id"], self.db)
        self.assertEqual(row["fee"], 40.0)

    def test_get_session_history_ordered_most_recent_first(self):
        slot = self._first_available_slot()
        e1 = self._park("HIST007", slot_id=slot["id"],
                        entry_time="2026-06-15 08:00:00")
        self._exit(session_id=e1["session_id"], exit_time="2026-06-15 09:00:00")
        e2 = self._park("HIST007", slot_id=slot["id"],
                        entry_time="2026-06-16 08:00:00")
        self._exit(session_id=e2["session_id"], exit_time="2026-06-16 09:00:00")

        history = database.get_session_history(
            vehicle_number="HIST007", db_path=self.db
        )
        self.assertEqual(history[0]["id"], e2["session_id"],
                         "Most recent session should be first")
        self.assertEqual(history[1]["id"], e1["session_id"])

    def test_parking_records_view_includes_completed_sessions(self):
        """The backward-compat SQL view must expose completed sessions."""
        entry = self._park("HIST008", entry_time="2026-06-15 08:00:00")
        self._exit(session_id=entry["session_id"], exit_time="2026-06-15 10:00:00")
        conn = database.get_connection(self.db)
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM parking_records WHERE vehicle_number = 'HIST008';"
        )
        row = cur.fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "COMPLETED")


# ===========================================================================
# 14. Database Error Handling
# ===========================================================================

class TestDatabaseErrorHandling(_TempDBMixin):
    """Verify that database-layer errors are correctly raised or propagated."""

    def test_unusable_database_path_surfaces_operational_error(self):
        """An invalid database location must fail explicitly, not create fallback data."""
        missing_parent = os.path.join(self.db, "missing-parent")
        with self.assertRaises(sqlite3.OperationalError):
            database.get_connection(os.path.join(missing_parent, "parking.db"))

    def test_start_session_with_invalid_slot_id_raises(self):
        with self.assertRaises(database.SlotNotFoundError):
            database.start_parking_session("ERR001", slot_id=999999,
                                           db_path=self.db)

    def test_start_session_with_occupied_slot_raises(self):
        slot = self._first_available_slot()
        database.start_parking_session("ERR002", slot["id"], db_path=self.db)
        with self.assertRaises(database.SlotNotAvailableError):
            database.start_parking_session("ERR003", slot["id"], db_path=self.db)

    def test_end_session_not_found_raises(self):
        with self.assertRaises(database.SessionNotFoundError):
            database.end_parking_session(session_id=999999, db_path=self.db)

    def test_end_already_completed_session_raises(self):
        slot = self._first_available_slot()
        sid = database.start_parking_session("ERR004", slot["id"],
                                             entry_time="2026-06-15 08:00:00",
                                             db_path=self.db)
        database.end_parking_session(sid, exit_time="2026-06-15 10:00:00",
                                     db_path=self.db)
        with self.assertRaises(database.InvalidOperationError):
            database.end_parking_session(sid, exit_time="2026-06-15 11:00:00",
                                         db_path=self.db)

    def test_cancel_session_not_found_raises(self):
        with self.assertRaises(database.SessionNotFoundError):
            database.cancel_parking_session(session_id=999999, db_path=self.db)

    def test_cancel_already_completed_session_raises(self):
        slot = self._first_available_slot()
        sid = database.start_parking_session("ERR005", slot["id"],
                                             db_path=self.db)
        database.end_parking_session(sid, db_path=self.db)
        with self.assertRaises(database.InvalidOperationError):
            database.cancel_parking_session(sid, db_path=self.db)

    def test_update_slot_status_unknown_slot_raises(self):
        with self.assertRaises(database.SlotNotFoundError):
            database.update_slot_status(999999, "Available", db_path=self.db)

    def test_update_slot_status_invalid_value_raises(self):
        slot = self._first_available_slot()
        with self.assertRaises(ValueError):
            database.update_slot_status(slot["id"], "InvalidStatus", db_path=self.db)

    def test_delete_nonexistent_session_raises(self):
        with self.assertRaises(database.SessionNotFoundError):
            database.delete_session_record(session_id=999999, db_path=self.db)

    def test_duplicate_slot_number_raises(self):
        with self.assertRaises(database.InvalidOperationError):
            database.create_slot("A-01", "Standard", db_path=self.db)

    def test_foreign_key_violation_raises_integrity_error(self):
        """Attempting to insert a session with a non-existent slot_id raises IntegrityError."""
        conn = database.get_connection(self.db)
        with self.assertRaises(sqlite3.IntegrityError):
            with conn:
                conn.execute(
                    "INSERT INTO parking_sessions "
                    "(vehicle_registration_number, slot_id, status) "
                    "VALUES ('FKVIOLATION', 999999, 'ACTIVE');"
                )
        conn.close()

    def test_calculate_fee_exit_before_entry_raises(self):
        with self.assertRaises(ValueError):
            database.calculate_fee(
                "2026-06-15 10:00:00", "2026-06-15 08:00:00",
                db_path=self.db
            )

    def test_defect_invalid_severity_raises(self):
        for bad in (0, 6, -1):
            with self.subTest(severity=bad):
                with self.assertRaises(ValueError):
                    database.log_defect("Cat", "Desc", bad, db_path=self.db)

    def test_update_session_details_not_found_raises(self):
        with self.assertRaises(database.SessionNotFoundError):
            database.update_session_details(
                session_id=99999, owner_name="Ghost", db_path=self.db
            )

    def test_get_session_by_id_returns_none_for_missing(self):
        result = database.get_session_by_id(99999, self.db)
        self.assertIsNone(result)

    def test_get_slot_by_id_returns_none_for_missing(self):
        result = database.get_slot_by_id(99999, self.db)
        self.assertIsNone(result)

    def test_get_slot_by_number_returns_none_for_missing(self):
        result = database.get_slot_by_number("ZZ-99", self.db)
        self.assertIsNone(result)

    def test_completed_session_is_removed_from_active_listing(self):
        entry = self._park("ACTIVE-LIFE-01")
        self.assertEqual(len(database.get_all_active_sessions(self.db)), 1)

        database.end_parking_session(
            entry["session_id"],
            exit_time="2026-01-01 10:00:00",
            fee=20.0,
            db_path=self.db,
        )

        self.assertEqual(database.get_all_active_sessions(self.db), [])
        history = database.get_session_history(
            db_path=self.db,
            vehicle_number="ACTIVE-LIFE-01",
        )
        self.assertEqual(history[0]["status"], "COMPLETED")

    def test_session_history_limit_is_respected(self):
        for index in range(3):
            entry = self._park(
                f"HISTORY-{index:02d}",
                entry_time=f"2026-01-0{index + 1} 08:00:00",
            )
            database.cancel_parking_session(entry["session_id"], db_path=self.db)

        history = database.get_session_history(db_path=self.db, limit=2)
        self.assertEqual(len(history), 2)


# ===========================================================================
# Entry point
# ===========================================================================

if __name__ == "__main__":
    unittest.main(verbosity=2)
