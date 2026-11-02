import os
import tempfile
import unittest

import auth
import database
import parking_service


class TestRuntimeSecurityFlow(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        self.db_path = handle.name
        database.init_db(self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_security_registers_and_scans_ticket(self):
        security = auth.authenticate("security", "Y8u3cqh_QXnBDW6PLdKL3z74", self.db_path)
        self.assertIsNotNone(security)

        entry = parking_service.register_vehicle_entry(
            "RT01SEC1001",
            owner_name="Security Flow",
            entry_time="2026-11-02 10:00:00",
            operator=security,
            db_path=self.db_path,
        )
        self.assertTrue(os.path.isfile(entry["qr_image_path"]))

        scanned = parking_service.scan_and_validate_qr_token(
            entry["qr_token"], operator=security, db_path=self.db_path
        )
        self.assertTrue(scanned["valid"])
        self.assertEqual(scanned["session_id"], entry["session_id"])
        scanned_from_image = parking_service.scan_and_validate_qr_token(
            entry["qr_image_path"], operator=security, db_path=self.db_path
        )
        self.assertEqual(scanned_from_image["session_id"], entry["session_id"])
        self.assertEqual(
            database.get_active_session_by_vehicle("RT01SEC1001", self.db_path)["slot_id"],
            entry["slot_id"],
        )

        with self.assertRaises(parking_service.ValidationError):
            parking_service.scan_and_validate_qr_token(
                "PKG-invalid-token", operator=security, db_path=self.db_path
            )


if __name__ == "__main__":
    unittest.main()
