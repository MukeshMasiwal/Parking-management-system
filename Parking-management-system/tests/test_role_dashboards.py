import os
import tempfile
import unittest

import auth
import database
from app.gui import ParkingApp, RoleDashboardView


ROLE_CREDENTIALS = {
    "admin": "tWZfBaHY9yQEjods_X3njAwB",
    "manager": "C1Wt7mXeSX0n_qAS7ci7IQ0V",
    "cashier": "0_AQJsPcZhu50hQPn2HXTiEw",
    "security": "Y8u3cqh_QXnBDW6PLdKL3z74",
    "valet": "jR4bb2gtzP2C3zUuYw3DQoIN",
}


class TestRoleDashboards(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        self.db_path = handle.name
        database.init_db(self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_each_role_gets_distinct_dashboard_and_navigation(self):
        dashboards = {}
        for username, password in ROLE_CREDENTIALS.items():
            user = auth.authenticate(username, password, self.db_path)
            app = ParkingApp(db_path=self.db_path, current_user=user)
            app.withdraw()
            try:
                self.assertIsInstance(app.views["Dashboard"], RoleDashboardView)
                dashboards[username] = tuple(sorted(app.allowed_screens))
            finally:
                app.destroy()

        self.assertIn("User Management", dashboards["admin"])
        self.assertNotIn("User Management", dashboards["cashier"])
        self.assertNotIn("Vehicle Entry", dashboards["cashier"])
        self.assertIn("Vehicle Entry", dashboards["security"])
        self.assertNotIn("Vehicle Entry", dashboards["valet"])
        self.assertNotEqual(dashboards["admin"], dashboards["valet"])


if __name__ == "__main__":
    unittest.main()
