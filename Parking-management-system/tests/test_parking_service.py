"""Business-logic tests for vehicle entry, parking, billing, and exit."""

import os
import tempfile
import unittest

import database
import parking_service


class TestParkingService(unittest.TestCase):
    """Exercise the service layer against an isolated SQLite database."""

    def setUp(self):
        self.temp_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_file.close()
        self.db_path = self.temp_file.name
        database.init_db(self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_entry_validates_registration_and_assigns_a_slot(self):
        with self.assertRaises(parking_service.ValidationError):
            parking_service.register_vehicle_entry("!!", db_path=self.db_path)

        result = parking_service.register_vehicle_entry(
            " mh12-ab-1234 ",
            vehicle_type="car",
            owner_name="  Jane Doe ",
            entry_time="2026-10-11 10:00:00",
            db_path=self.db_path,
        )

        self.assertEqual(result["vehicle_registration_number"], "MH12-AB-1234")
        self.assertEqual(result["owner_name"], "Jane Doe")
        self.assertEqual(result["status"], "ACTIVE")
        self.assertEqual(
            database.get_slot_by_id(result["slot_id"], self.db_path)["status"],
            "Occupied",
        )

    def test_entry_rejects_non_text_owner_name_and_vehicle_type(self):
        with self.assertRaises(parking_service.ValidationError):
            parking_service.register_vehicle_entry(
                "MH12AB1234",
                owner_name=123,
                db_path=self.db_path,
            )
        with self.assertRaises(parking_service.ValidationError):
            parking_service.register_vehicle_entry(
                "MH12AB1235",
                vehicle_type=object(),
                db_path=self.db_path,
            )

    def test_duplicate_active_entry_is_rejected(self):
        first = parking_service.register_vehicle_entry("DL01XY9999", db_path=self.db_path)

        with self.assertRaises(parking_service.DuplicateActiveSessionError):
            parking_service.register_vehicle_entry("dl01xy9999", db_path=self.db_path)

        self.assertEqual(len(database.get_all_active_sessions(self.db_path)), 1)
        self.assertEqual(
            database.get_slot_by_id(first["slot_id"], self.db_path)["status"],
            "Occupied",
        )

    def test_capacity_limit_is_enforced(self):
        slots = database.get_all_slots(self.db_path)
        for slot in slots[1:]:
            database.update_slot_status(slot["id"], "Maintenance", self.db_path)

        parking_service.register_vehicle_entry("KA03AA1111", db_path=self.db_path)
        with self.assertRaises(parking_service.ParkingCapacityFullError):
            parking_service.register_vehicle_entry("KA03BB2222", db_path=self.db_path)

    def test_approved_fee_policy_is_applied(self):
        free = parking_service.calculate_duration_and_fee(
            "2026-10-11 10:00:00",
            "2026-10-11 10:15:00",
            db_path=self.db_path,
        )
        self.assertEqual(free["fee"], 0.0)
        self.assertEqual(free["billable_hours"], 0)

        rounded = parking_service.calculate_duration_and_fee(
            "2026-10-11 10:00:00",
            "2026-10-11 11:15:00",
            vehicle_type="Two-Wheeler",
            db_path=self.db_path,
        )
        self.assertEqual(rounded["billable_hours"], 2)
        self.assertEqual(rounded["fee"], 20.0)

    def test_fee_at_grace_period_boundary_remains_free(self):
        result = parking_service.calculate_duration_and_fee(
            "2026-10-11 10:00:00",
            "2026-10-11 10:15:00",
            db_path=self.db_path,
        )
        self.assertEqual(result["duration_minutes"], 15)
        self.assertEqual(result["billable_hours"], 0)
        self.assertEqual(result["final_fee"], 0.0)

    def test_failed_exit_keeps_slot_occupied(self):
        entry = parking_service.register_vehicle_entry(
            "UP16CC3333",
            entry_time="2026-10-11 12:00:00",
            db_path=self.db_path,
        )

        with self.assertRaises(parking_service.InvalidExitError):
            parking_service.record_vehicle_exit(
                session_id=entry["session_id"],
                exit_time="2026-10-11 11:00:00",
                db_path=self.db_path,
            )

        self.assertEqual(
            database.get_slot_by_id(entry["slot_id"], self.db_path)["status"],
            "Occupied",
        )
        self.assertEqual(
            database.get_session_by_id(entry["session_id"], self.db_path)["status"],
            "ACTIVE",
        )

    def test_exit_releases_slot_and_preserves_history(self):
        entry = parking_service.register_vehicle_entry(
            "HR26DD4444",
            owner_name="Alex",
            entry_time="2026-10-11 09:00:00",
            db_path=self.db_path,
        )

        receipt = parking_service.record_vehicle_exit(
            session_id=entry["session_id"],
            exit_time="2026-10-11 10:30:00",
            db_path=self.db_path,
        )

        self.assertEqual(receipt["final_fee"], 40.0)
        self.assertEqual(receipt["status"], "COMPLETED")
        self.assertEqual(
            database.get_slot_by_id(entry["slot_id"], self.db_path)["status"],
            "Available",
        )
        history = parking_service.get_vehicle_history("hr26dd4444", db_path=self.db_path)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["status"], "COMPLETED")

    def test_search_update_and_safe_delete(self):
        entry = parking_service.register_vehicle_entry(
            "GJ05EE5555",
            owner_name="Sam",
            db_path=self.db_path,
        )
        found = parking_service.search_parking_records(
            keyword="sam", db_path=self.db_path
        )
        self.assertEqual([row["id"] for row in found], [entry["session_id"]])

        updated = parking_service.update_permitted_vehicle_info(
            entry["session_id"],
            vehicle_type="EV",
            owner_contact="555-0100",
            db_path=self.db_path,
        )
        self.assertEqual(updated["vehicle_type"], "EV")
        self.assertEqual(updated["owner_contact"], "555-0100")

        with self.assertRaises(parking_service.ValidationError):
            parking_service.update_permitted_vehicle_info(
                entry["session_id"],
                owner_name=123,
                db_path=self.db_path,
            )
        unchanged = database.get_session_by_id(entry["session_id"], self.db_path)
        self.assertEqual(unchanged["owner_name"], "Sam")

        with self.assertRaises(parking_service.UnsafeDeletionError):
            parking_service.delete_parking_record_safely(
                entry["session_id"], db_path=self.db_path
            )

        parking_service.cancel_active_session_safely(
            entry["session_id"], db_path=self.db_path
        )
        parking_service.delete_parking_record_safely(
            entry["session_id"], db_path=self.db_path
        )
        self.assertIsNone(database.get_session_by_id(entry["session_id"], self.db_path))

    def test_explicit_slot_assignment_and_validation(self):
        # 1. Explicit slot by ID
        slots = database.get_available_slots(self.db_path)
        first_slot = slots[0]
        res1 = parking_service.register_vehicle_entry(
            "MH01AA1001",
            slot_id=first_slot["id"],
            db_path=self.db_path,
        )
        self.assertEqual(res1["slot_id"], first_slot["id"])
        self.assertEqual(res1["slot_number"], first_slot["slot_number"])

        # 2. Attempt to explicitly pick the now-occupied slot
        with self.assertRaises(parking_service.SlotNotAvailableError):
            parking_service.register_vehicle_entry(
                "MH01BB2002",
                slot_id=first_slot["id"],
                db_path=self.db_path,
            )

        # 3. Explicit slot by slot_number
        second_slot = slots[1]
        res2 = parking_service.register_vehicle_entry(
            "MH01BB2002",
            slot_number=second_slot["slot_number"],
            db_path=self.db_path,
        )
        self.assertEqual(res2["slot_number"], second_slot["slot_number"])

        # 4. Non-existent slot ID or slot number
        with self.assertRaises(parking_service.SlotNotFoundError):
            parking_service.register_vehicle_entry(
                "MH01CC3003",
                slot_id=99999,
                db_path=self.db_path,
            )
        with self.assertRaises(parking_service.SlotNotFoundError):
            parking_service.register_vehicle_entry(
                "MH01CC3003",
                slot_number="INVALID-SLOT",
                db_path=self.db_path,
            )

    def test_reentry_after_completed_exit(self):
        # Entry 1
        entry1 = parking_service.register_vehicle_entry(
            "KA05ZZ1234",
            entry_time="2026-10-11 08:00:00",
            db_path=self.db_path,
        )
        self.assertEqual(entry1["status"], "ACTIVE")

        # Exit 1
        receipt = parking_service.record_vehicle_exit(
            session_id=entry1["session_id"],
            exit_time="2026-10-11 09:00:00",
            db_path=self.db_path,
        )
        self.assertEqual(receipt["status"], "COMPLETED")

        # Entry 2 with same registration number (now allowed)
        entry2 = parking_service.register_vehicle_entry(
            "ka05zz1234",
            entry_time="2026-10-11 14:00:00",
            db_path=self.db_path,
        )
        self.assertNotEqual(entry1["session_id"], entry2["session_id"])
        self.assertEqual(entry2["status"], "ACTIVE")

        history = parking_service.get_vehicle_history("KA05ZZ1234", db_path=self.db_path)
        self.assertEqual(len(history), 2)
        statuses = [s["status"] for s in history]
        self.assertIn("COMPLETED", statuses)
        self.assertIn("ACTIVE", statuses)

    def test_exit_by_plate_and_discount_rules(self):
        entry = parking_service.register_vehicle_entry(
            "DL04AA4321",
            vehicle_type="Car",
            entry_time="2026-10-11 10:00:00",
            db_path=self.db_path,
        )

        # Invalid discounts
        with self.assertRaises(parking_service.ValidationError):
            parking_service.record_vehicle_exit(
                vehicle_registration_number="dl04aa4321",
                exit_time="2026-10-11 11:30:00",
                discount_amount=-5.0,
                db_path=self.db_path,
            )

        with self.assertRaises(parking_service.ValidationError):
            parking_service.record_vehicle_exit(
                vehicle_registration_number="dl04aa4321",
                exit_time="2026-10-11 11:30:00",
                discount_amount=100.0,  # exceeds fee of 40.0 (2 hrs @ 20/hr)
                db_path=self.db_path,
            )

        # Successful exit by plate with discount
        receipt = parking_service.record_vehicle_exit(
            vehicle_registration_number="dl04aa4321",
            exit_time="2026-10-11 11:30:00",
            discount_amount=10.0,
            db_path=self.db_path,
        )
        self.assertEqual(receipt["base_fee"], 40.0)
        self.assertEqual(receipt["discount_applied"], 10.0)
        self.assertEqual(receipt["final_fee"], 30.0)
        self.assertEqual(receipt["status"], "COMPLETED")

    def test_non_finite_discount_is_rejected(self):
        entry = parking_service.register_vehicle_entry(
            "DL04AA4322",
            entry_time="2026-10-11 10:00:00",
            db_path=self.db_path,
        )
        for invalid_discount in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(discount=invalid_discount):
                with self.assertRaises(parking_service.ValidationError):
                    parking_service.record_vehicle_exit(
                        session_id=entry["session_id"],
                        exit_time="2026-10-11 11:30:00",
                        discount_amount=invalid_discount,
                        db_path=self.db_path,
                    )

    def test_explicit_slot_number_ignores_surrounding_whitespace(self):
        slot = database.get_available_slots(self.db_path)[0]
        result = parking_service.register_vehicle_entry(
            "MH12AB1236",
            slot_number=f"  {slot['slot_number']}  ",
            db_path=self.db_path,
        )
        self.assertEqual(result["slot_number"], slot["slot_number"])

    def test_exit_nonexistent_and_completed_session_errors(self):
        # Non-existent session ID
        with self.assertRaises(parking_service.SessionNotFoundError):
            parking_service.record_vehicle_exit(
                session_id=8888,
                exit_time="2026-10-11 10:00:00",
                db_path=self.db_path,
            )

        # Non-existent vehicle plate
        with self.assertRaises(parking_service.SessionNotFoundError):
            parking_service.record_vehicle_exit(
                vehicle_registration_number="XX99YY9999",
                exit_time="2026-10-11 10:00:00",
                db_path=self.db_path,
            )

        # Already completed session
        entry = parking_service.register_vehicle_entry(
            "WB02XY1111",
            entry_time="2026-10-11 08:00:00",
            db_path=self.db_path,
        )
        parking_service.record_vehicle_exit(
            session_id=entry["session_id"],
            exit_time="2026-10-11 09:00:00",
            db_path=self.db_path,
        )
        with self.assertRaises(parking_service.InvalidExitError):
            parking_service.record_vehicle_exit(
                session_id=entry["session_id"],
                exit_time="2026-10-11 10:00:00",
                db_path=self.db_path,
            )

    def test_configurable_rates_and_vehicle_categories(self):
        # 1. Heavy vehicle rate ($40/hr)
        calc_heavy = parking_service.calculate_duration_and_fee(
            "2026-10-11 10:00:00",
            "2026-10-11 11:30:00",
            vehicle_type="Heavy",
            db_path=self.db_path,
        )
        self.assertEqual(calc_heavy["hourly_rate"], 40.0)
        self.assertEqual(calc_heavy["billable_hours"], 2)
        self.assertEqual(calc_heavy["fee"], 80.0)

        # 2. Dynamic rate update via application settings
        database.set_setting("rate_car", "35.0", db_path=self.db_path)
        calc_car = parking_service.calculate_duration_and_fee(
            "2026-10-11 10:00:00",
            "2026-10-11 11:00:00",
            vehicle_type="Car",
            db_path=self.db_path,
        )
        self.assertEqual(calc_car["hourly_rate"], 35.0)
        self.assertEqual(calc_car["fee"], 35.0)

        # 3. Exact 15 min grace vs 16 min transition
        calc_15m = parking_service.calculate_duration_and_fee(
            "2026-10-11 10:00:00",
            "2026-10-11 10:15:00",
            vehicle_type="Car",
            db_path=self.db_path,
        )
        self.assertEqual(calc_15m["fee"], 0.0)
        self.assertEqual(calc_15m["billable_hours"], 0)

        calc_16m = parking_service.calculate_duration_and_fee(
            "2026-10-11 10:00:00",
            "2026-10-11 10:16:00",
            vehicle_type="Car",
            db_path=self.db_path,
        )
        self.assertEqual(calc_16m["billable_hours"], 1)
        self.assertEqual(calc_16m["fee"], 35.0)

    def test_capacity_status_metrics(self):
        initial = parking_service.get_parking_capacity_status(self.db_path)
        self.assertEqual(initial["total_slots"], 50)
        self.assertEqual(initial["occupied_slots"], 0)
        self.assertEqual(initial["available_slots"], 50)
        self.assertEqual(initial["occupancy_rate_percent"], 0.0)

        entry = parking_service.register_vehicle_entry("TN07AA5555", db_path=self.db_path)

        after_entry = parking_service.get_parking_capacity_status(self.db_path)
        self.assertEqual(after_entry["occupied_slots"], 1)
        self.assertEqual(after_entry["available_slots"], 49)
        self.assertEqual(after_entry["active_sessions"], 1)
        self.assertEqual(after_entry["occupancy_rate_percent"], 2.0)

        parking_service.record_vehicle_exit(
            session_id=entry["session_id"],
            db_path=self.db_path,
        )
        after_exit = parking_service.get_parking_capacity_status(self.db_path)
        self.assertEqual(after_exit["occupied_slots"], 0)
        self.assertEqual(after_exit["available_slots"], 50)
        self.assertEqual(after_exit["active_sessions"], 0)

    def test_safe_deletion_conditions_with_audit_override(self):
        # Create completed billed session
        entry = parking_service.register_vehicle_entry(
            "GA01BB6666",
            entry_time="2026-10-11 08:00:00",
            db_path=self.db_path,
        )
        parking_service.record_vehicle_exit(
            session_id=entry["session_id"],
            exit_time="2026-10-11 10:00:00",
            db_path=self.db_path,
        )

        # 1. Attempt delete without admin override
        with self.assertRaises(parking_service.UnsafeDeletionError):
            parking_service.delete_parking_record_safely(
                entry["session_id"],
                admin_override=False,
                db_path=self.db_path,
            )

        # 2. Attempt delete with admin override but missing/empty audit reason
        with self.assertRaises(parking_service.UnsafeDeletionError):
            parking_service.delete_parking_record_safely(
                entry["session_id"],
                admin_override=True,
                audit_reason="",
                db_path=self.db_path,
            )
        with self.assertRaises(parking_service.UnsafeDeletionError):
            parking_service.delete_parking_record_safely(
                entry["session_id"],
                admin_override=True,
                audit_reason="   ",
                db_path=self.db_path,
            )

        # 3. Successful administrative override deletion with reason
        parking_service.delete_parking_record_safely(
            entry["session_id"],
            admin_override=True,
            audit_reason="Compliance data purge request #412",
            db_path=self.db_path,
        )
        self.assertIsNone(database.get_session_by_id(entry["session_id"], self.db_path))

        # 4. Deleting non-existent session
        with self.assertRaises(parking_service.SessionNotFoundError):
            parking_service.delete_parking_record_safely(99999, db_path=self.db_path)

    def test_slot_type_allocation_preferences(self):
        # Two-Wheeler gets Two-Wheeler slot (starts with 'C-')
        bike = parking_service.register_vehicle_entry(
            "KA01BIKE1",
            vehicle_type="Two-Wheeler",
            db_path=self.db_path,
        )
        self.assertTrue(bike["slot_number"].startswith("C-"))

        # EV gets EV slot (starts with 'E-')
        ev = parking_service.register_vehicle_entry(
            "KA01EVEV1",
            vehicle_type="EV",
            db_path=self.db_path,
        )
        self.assertTrue(ev["slot_number"].startswith("E-"))

        # Standard Car gets Standard slot (starts with 'A-')
        car = parking_service.register_vehicle_entry(
            "KA01CAR1",
            vehicle_type="Car",
            db_path=self.db_path,
        )
        self.assertTrue(car["slot_number"].startswith("A-"))

    def test_concurrent_slot_conflict_protection(self):
        # Register a car into slot A-01
        first = parking_service.register_vehicle_entry(
            "KL01AA1111",
            slot_number="A-01",
            db_path=self.db_path,
        )
        self.assertEqual(first["slot_number"], "A-01")

        # Second entry attempting same slot directly raises SlotNotAvailableError
        with self.assertRaises(parking_service.SlotNotAvailableError):
            parking_service.register_vehicle_entry(
                "KL02BB2222",
                slot_id=first["slot_id"],
                db_path=self.db_path,
            )

        # Slot status remains Occupied by the first vehicle
        slot = database.get_slot_by_id(first["slot_id"], self.db_path)
        self.assertEqual(slot["status"], "Occupied")

    def test_owner_contact_validation(self):
        """Verifies contact number sanitization and rejection of invalid formats."""
        # 1. Valid contacts
        self.assertEqual(parking_service.validate_contact_number(None), None)
        self.assertEqual(parking_service.validate_contact_number(""), None)
        self.assertEqual(parking_service.validate_contact_number("   "), None)
        self.assertEqual(parking_service.validate_contact_number("+1-800-555-0199"), "+1-800-555-0199")
        self.assertEqual(parking_service.validate_contact_number("(022) 2456-7890"), "(022) 2456-7890")

        # 2. Invalid contacts raise ValidationError
        with self.assertRaises(parking_service.ValidationError):
            parking_service.validate_contact_number("12345")  # Too short (< 7 chars)
        with self.assertRaises(parking_service.ValidationError):
            parking_service.validate_contact_number("call-me-now@mail")  # Illegal chars
        with self.assertRaises(parking_service.ValidationError):
            parking_service.validate_contact_number("+12-34-56")  # Only 6 digits (< 7 digits)

        # 3. Rejection during vehicle registration
        with self.assertRaises(parking_service.ValidationError):
            parking_service.register_vehicle_entry(
                "KA01VALID1",
                owner_contact="bad-contact",
                db_path=self.db_path,
            )

    def test_calculate_duration_and_fee_discount_rules(self):
        """Verifies discount calculation, ceiling enforcement, and negative discount rejection."""
        # 1. Normal discount application on a 2-hour Car stay ($40 base)
        res = parking_service.calculate_duration_and_fee(
            entry_time_str="2026-10-10 10:00:00",
            exit_time_str="2026-10-10 12:00:00",
            vehicle_type="Car",
            discount_amount=10.0,
            db_path=self.db_path,
        )
        self.assertEqual(res["base_fee"], 40.0)
        self.assertEqual(res["discount_applied"], 10.0)
        self.assertEqual(res["final_fee"], 30.0)
        self.assertEqual(res["fee"], 30.0)

        # 2. Discount exceeding base fee raises ValidationError
        with self.assertRaises(parking_service.ValidationError):
            parking_service.calculate_duration_and_fee(
                entry_time_str="2026-10-10 10:00:00",
                exit_time_str="2026-10-10 12:00:00",
                vehicle_type="Car",
                discount_amount=45.0,
                db_path=self.db_path,
            )

        # 3. Negative discount raises ValidationError
        with self.assertRaises(parking_service.ValidationError):
            parking_service.calculate_duration_and_fee(
                entry_time_str="2026-10-10 10:00:00",
                exit_time_str="2026-10-10 12:00:00",
                vehicle_type="Car",
                discount_amount=-5.0,
                db_path=self.db_path,
            )

        # 4. Non-numeric discount raises ValidationError
        with self.assertRaises(parking_service.ValidationError):
            parking_service.calculate_duration_and_fee(
                entry_time_str="2026-10-10 10:00:00",
                exit_time_str="2026-10-10 12:00:00",
                vehicle_type="Car",
                discount_amount="invalid_discount",  # type: ignore
                db_path=self.db_path,
            )


if __name__ == "__main__":
    unittest.main()
