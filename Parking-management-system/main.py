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
from auth import AuthenticationError, authenticate
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

    login = ctk.CTk()
    login.title("Parking Management System - Sign In")
    login.geometry("520x520")
    login.resizable(False, False)

    ctk.CTkLabel(
        login,
        text="Parking Management System",
        font=ctk.CTkFont(size=22, weight="bold"),
    ).pack(pady=(35, 8))
    ctk.CTkLabel(login, text="Sign in to continue").pack(pady=(0, 20))

    username_entry = ctk.CTkEntry(login, width=280, placeholder_text="Username")
    username_entry.pack(pady=6)
    password_entry = ctk.CTkEntry(
        login, width=280, placeholder_text="Password", show="*"
    )
    password_entry.pack(pady=6)
    status_label = ctk.CTkLabel(login, text="", text_color="#c0392b")
    status_label.pack(pady=(4, 8))

    def sign_in():
        try:
            user = authenticate(username_entry.get(), password_entry.get())
        except AuthenticationError:
            status_label.configure(text="Authentication service is unavailable.")
            password_entry.delete(0, "end")
            return
        if user is None:
            status_label.configure(text="Invalid username or password.")
            password_entry.delete(0, "end")
            return
        login.destroy()
        app = ParkingApp(current_user=user)
        app.mainloop()

    ctk.CTkButton(login, text="Sign In", width=280, command=sign_in).pack(pady=8)

    def use_demo_account(username, password):
        username_entry.delete(0, "end")
        username_entry.insert(0, username)
        password_entry.delete(0, "end")
        password_entry.insert(0, password)
        sign_in()

    demo_mode = os.getenv("PARKING_DEMO_MODE", "").strip().lower() in {
        "1", "true", "yes", "on"
    }
    demo_passwords = {
        role: os.getenv(f"PARKING_DEMO_PASSWORD_{role.upper()}", "")
        for role in ("admin", "manager", "cashier", "security", "valet")
    }
    if demo_mode and all(demo_passwords.values()):
        ctk.CTkLabel(
            login,
            text="Development demo login",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(pady=(14, 6))
        demo_buttons = ctk.CTkFrame(login, fg_color="transparent")
        demo_buttons.pack(fill="x", padx=35, pady=(0, 10))
        for index, role in enumerate(("admin", "manager", "cashier", "security", "valet")):
            button = ctk.CTkButton(
                demo_buttons,
                text=f"Login as {role.capitalize()}",
                command=lambda r=role: use_demo_account(r, demo_passwords[r]),
            )
            button.grid(row=index // 2, column=index % 2, padx=5, pady=4, sticky="ew")
        demo_buttons.grid_columnconfigure(0, weight=1)
        demo_buttons.grid_columnconfigure(1, weight=1)
    else:
        ctk.CTkLabel(
            login,
            text="Demo shortcuts are disabled. Use username and password.",
            text_color="#7f8c8d",
        ).pack(pady=12)
    username_entry.focus()
    login.mainloop()


if __name__ == "__main__":
    main()
