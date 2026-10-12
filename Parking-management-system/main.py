"""
Parking Management System - Desktop Application Entry Point
============================================================
Launches the CustomTkinter Graphical User Interface integrating
with the SQLite database and business logic layer.
"""

import sys
import os

# Ensure package directory is available
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import customtkinter as ctk
from database import init_db
from app.gui import (
    ParkingApp,
    DashboardView,
    VehicleEntryView,
    VehicleExitView,
    ParkingRecordsView,
    ParkingSlotsView,
    HelpView,
    AboutView,
)


def main():
    """Initializes the database and launches the GUI application."""
    init_db()

    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")

    app = ParkingApp()
    app.mainloop()


if __name__ == "__main__":
    main()
