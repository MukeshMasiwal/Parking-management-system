import os
import tempfile
import unittest

import auth
import database
import parking_service


class TestRuntimeCashierExitFlow(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        self.db_path = handle.name
        database.init_db(self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_cashier_scans_calculates_pays_and_releases_slot(self):
        cashier = auth.authenticate("cashier", "0_AQJsPcZhu50hQPn2HXTiEw", self.db_path)
        entry = parking_service.register_vehicle_entry(
            "RT02CASH1001",
            entry_time="2026-11-02 08:00:00",
            db_path=self.db_path,
        )
        scanned = parking_service.scan_and_validate_qr_token(
            entry["qr_token"], operator=cashier, db_path=self.db_path
        )
        self.assertGreater(scanned["current_fee"], 0)

        receipt = parking_service.record_vehicle_exit(
            qr_token=entry["qr_token"],
            exit_time="2026-11-02 10:00:00",
            payment_method="UPI",
            operator=cashier,
            db_path=self.db_path,
        )
        self.assertIn("OFFICIAL RECEIPT", receipt["receipt_text"])
        completed = database.get_session_by_id(entry["session_id"], self.db_path)
        self.assertEqual(completed["payment_status"], "PAID")
        self.assertEqual(
            database.get_slot_by_id(entry["slot_id"], self.db_path)["status"],
            "Available",
        )

        with self.assertRaises(parking_service.InvalidExitError):
            parking_service.record_vehicle_exit(
                session_id=entry["session_id"],
                operator=cashier,
                db_path=self.db_path,
            )


if __name__ == "__main__":
    unittest.main()
