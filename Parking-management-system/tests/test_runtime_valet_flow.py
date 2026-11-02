import os
import tempfile
import unittest

import auth
import database
import parking_service


class TestRuntimeValetFlow(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        self.db_path = handle.name
        database.init_db(self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_valet_lifecycle_and_invalid_transition(self):
        valet = auth.authenticate("valet", "jR4bb2gtzP2C3zUuYw3DQoIN", self.db_path)
        security = auth.authenticate("security", "Y8u3cqh_QXnBDW6PLdKL3z74", self.db_path)
        entry = parking_service.register_vehicle_entry(
            "RT03VALET1001",
            is_valet=True,
            valet_user_id=valet.user_id,
            operator=security,
            db_path=self.db_path,
        )
        assignment = database.get_valet_assignment_by_session_id(
            entry["session_id"], self.db_path
        )

        with self.assertRaises(database.InvalidOperationError):
            parking_service.valet_complete_parking(
                valet, assignment["id"], db_path=self.db_path
            )

        parking_service.valet_accept_handover(valet, assignment["id"], db_path=self.db_path)
        parking_service.valet_start_parking(valet, assignment["id"], db_path=self.db_path)
        parking_service.valet_complete_parking(valet, assignment["id"], db_path=self.db_path)
        parking_service.request_valet_retrieval(valet, assignment["id"], db_path=self.db_path)
        parking_service.valet_retrieve_vehicle(valet, assignment["id"], db_path=self.db_path)
        parking_service.valet_deliver_vehicle(valet, assignment["id"], db_path=self.db_path)

        final_assignment = database.get_valet_assignment_by_id(
            assignment["id"], self.db_path
        )
        self.assertEqual(final_assignment["status"], "DELIVERED")
        self.assertEqual(len(database.get_valet_activity_logs(assignment["id"], db_path=self.db_path)), 7)


if __name__ == "__main__":
    unittest.main()
