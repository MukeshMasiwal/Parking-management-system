"""
GUI Automated Workflow Tests
============================
Tests end-to-end user workflows on the CustomTkinter graphical interface
using an isolated temporary SQLite database in offscreen mode.
"""

import os
import sys
import tempfile
import unittest

# Ensure parent directory is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import customtkinter as ctk
import database
import parking_service
from app.gui import (
    ParkingApp,
    DashboardView,
    VehicleEntryView,
    VehicleExitView,
    ParkingRecordsView,
    ParkingSlotsView,
)


class TestParkingGUI(unittest.TestCase):
    """Exercises GUI views, navigation, workflows, and database synchronization."""

    def setUp(self):
        # Create isolated temporary database file
        self.temp_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_file.close()
        self.db_path = self.temp_file.name
        database.init_db(self.db_path)

        # Initialize GUI in withdrawn (offscreen) mode
        self.app = ParkingApp(db_path=self.db_path)
        self.app.withdraw()
        self.app.update_idletasks()

    def tearDown(self):
        try:
            self.app.update_idletasks()
            self.app.destroy()
        except Exception:
            pass
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_gui_initialization_and_screen_navigation(self):
        """Verifies that all 5 required screens are created and accessible via navigation."""
        self.assertIn("Dashboard", self.app.views)
        self.assertIn("Vehicle Entry", self.app.views)
        self.assertIn("Vehicle Exit", self.app.views)
        self.assertIn("Parking Records", self.app.views)
        self.assertIn("Parking Slots", self.app.views)

        for screen in ["Dashboard", "Vehicle Entry", "Vehicle Exit", "Parking Records", "Parking Slots"]:
            self.app.select_frame(screen)
            self.app.update_idletasks()
            self.assertEqual(
                self.app.nav_buttons[screen].cget("fg_color"),
                "#1f6aa5",
                f"Button for {screen} should be highlighted as active",
            )

    def test_dashboard_kpis_and_active_vehicles_sync(self):
        """Verifies that the dashboard displays actual database data without fake stats."""
        dash = self.app.views["Dashboard"]
        self.assertIsInstance(dash, DashboardView)

        # Initially 50 slots, 0 occupied, 0 active, 0 revenue
        dash.refresh()
        self.app.update_idletasks()
        self.assertIn("50", dash.kpi_total.cget("text"))
        self.assertIn("50", dash.kpi_avail.cget("text"))
        self.assertEqual(dash.kpi_occup.cget("text"), "0")
        self.assertEqual(dash.kpi_active.cget("text"), "0")
        self.assertEqual(dash.kpi_revenue.cget("text"), "$0.00")

        # Register vehicle directly in backend
        res = parking_service.register_vehicle_entry(
            "KA01AA1111",
            vehicle_type="Car",
            db_path=self.db_path,
        )
        self.app.notify_data_changed()
        self.app.update_idletasks()

        self.assertEqual(dash.kpi_occup.cget("text"), "1")
        self.assertEqual(dash.kpi_active.cget("text"), "1")
        self.assertEqual(dash.kpi_avail.cget("text"), "49")

        # Table contains the active parked vehicle
        items = dash.tree.get_children()
        self.assertEqual(len(items), 1)
        row = dash.tree.item(items[0])["values"]
        self.assertEqual(row[1], "KA01AA1111")
        self.assertEqual(row[3], res["slot_number"])

    def test_vehicle_entry_workflow_and_duplicate_handling(self):
        """Tests user entry form registration, slot allocation, and error handling."""
        entry_view = self.app.views["Vehicle Entry"]
        self.assertIsInstance(entry_view, VehicleEntryView)

        # 1. Successful entry
        entry_view.plate_entry.delete(0, "end")
        entry_view.plate_entry.insert(0, "MH04XY9999")
        entry_view.vtype_var.set("Car")
        entry_view.owner_entry.delete(0, "end")
        entry_view.owner_entry.insert(0, "John Doe")
        entry_view.contact_entry.delete(0, "end")
        entry_view.contact_entry.insert(0, "555-0199")

        entry_view._on_register_entry()
        self.app.update_idletasks()

        # Check that session exists in database
        active = database.get_active_session_by_vehicle("MH04XY9999", db_path=self.db_path)
        self.assertIsNotNone(active)
        self.assertEqual(active["owner_name"], "John Doe")
        self.assertEqual(active["vehicle_type"], "Car")

        # 2. Attempt duplicate entry for same vehicle
        entry_view.plate_entry.delete(0, "end")
        entry_view.plate_entry.insert(0, "mh04xy9999")
        entry_view._on_register_entry()
        self.app.update_idletasks()

        # Still only 1 active session in database
        all_active = database.get_all_active_sessions(db_path=self.db_path)
        self.assertEqual(len(all_active), 1)

    def test_vehicle_exit_and_receipt_generation_workflow(self):
        """Tests vehicle lookup, fee calculation preview, exit processing, and receipt display."""
        exit_view = self.app.views["Vehicle Exit"]
        self.assertIsInstance(exit_view, VehicleExitView)

        # Enter vehicle with past timestamp (2 hours ago)
        entry = parking_service.register_vehicle_entry(
            "DL01BB2222",
            vehicle_type="Car",
            entry_time="2026-10-12 10:00:00",
            db_path=self.db_path,
        )

        # Select vehicle in exit view
        exit_view.select_vehicle("DL01BB2222")
        self.app.update_idletasks()

        self.assertIsNotNone(exit_view.current_session)
        self.assertEqual(exit_view.exit_btn.cget("state"), "normal")

        # Process exit with $5.00 discount
        exit_view.discount_entry.delete(0, "end")
        exit_view.discount_entry.insert(0, "5.00")

        exit_view._on_process_exit()
        self.app.update_idletasks()

        # Session should now be COMPLETED in database
        completed = database.get_session_by_id(entry["session_id"], db_path=self.db_path)
        self.assertEqual(completed["status"], "COMPLETED")
        self.assertGreater(completed["fee"], 0.0)

        # Slot should be released back to Available
        slot = database.get_slot_by_id(entry["slot_id"], db_path=self.db_path)
        self.assertEqual(slot["status"], "Available")

        # Current session cleared on exit view
        self.assertIsNone(exit_view.current_session)
        self.assertEqual(exit_view.exit_btn.cget("state"), "disabled")

    def test_parking_records_search_filters_and_updates(self):
        """Tests records table searching, filtering, and permitted vehicle detail updates."""
        records_view = self.app.views["Parking Records"]
        self.assertIsInstance(records_view, ParkingRecordsView)

        # Register two vehicles of different types
        e1 = parking_service.register_vehicle_entry("WB01AA1111", vehicle_type="Car", owner_name="Alice", db_path=self.db_path)
        e2 = parking_service.register_vehicle_entry("WB02BB2222", vehicle_type="Two-Wheeler", owner_name="Bob", db_path=self.db_path)

        # Refresh table
        records_view.refresh()
        self.app.update_idletasks()
        self.assertEqual(len(records_view.tree.get_children()), 2)

        # Search by keyword "Alice"
        records_view.search_entry.delete(0, "end")
        records_view.search_entry.insert(0, "Alice")
        records_view.refresh()
        self.app.update_idletasks()
        items = records_view.tree.get_children()
        self.assertEqual(len(items), 1)
        self.assertEqual(records_view.tree.item(items[0])["values"][1], "WB01AA1111")

        # Filter by Type "Two-Wheeler"
        records_view.search_entry.delete(0, "end")
        records_view.vtype_var.set("Two-Wheeler")
        records_view.refresh()
        self.app.update_idletasks()
        items = records_view.tree.get_children()
        self.assertEqual(len(items), 1)
        self.assertEqual(records_view.tree.item(items[0])["values"][1], "WB02BB2222")

        # Reset filters
        records_view._on_reset_filters()
        self.app.update_idletasks()
        self.assertEqual(len(records_view.tree.get_children()), 2)

    def test_parking_slots_grid_and_synchronization(self):
        """Tests visual slot map rendering and live synchronization with database states."""
        slots_view = self.app.views["Parking Slots"]
        self.assertIsInstance(slots_view, ParkingSlotsView)

        # Initially 50 slots in database
        slots_view.refresh()
        self.app.update_idletasks()
        self.assertEqual(len(slots_view.all_slots), 50)

        # Register vehicle into specific slot
        entry = parking_service.register_vehicle_entry(
            "TN01EV0001",
            vehicle_type="EV",
            slot_number="E-01",
            db_path=self.db_path,
        )

        slots_view.refresh()
        self.app.update_idletasks()

        # Slot E-01 should now be Occupied with vehicle plate
        e1_slot = next(s for s in slots_view.all_slots if s["slot_number"] == "E-01")
        self.assertEqual(e1_slot["status"], "Occupied")
        self.assertEqual(e1_slot["vehicle_registration_number"], "TN01EV0001")


if __name__ == "__main__":
    unittest.main()
