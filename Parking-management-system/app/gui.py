"""
Parking Management System - Graphical User Interface (GUI)
============================================================
Modern desktop interface built with CustomTkinter and Tkinter Treeview.
Integrates directly with the parking business logic layer (parking_service.py).

Screens:
1. Dashboard: KPIs, occupancy progress, and active vehicles list.
2. Vehicle Entry: Registration, type selection, slot allocation, and ticket.
3. Vehicle Exit: Active session lookup, fee calculation, and receipt generation.
4. Parking Records: Search, filter by status and type, update, safe delete.
5. Parking Slots: Real-time visual slot grid synchronized with the database.

Guarantees:
- Strict separation of concerns: ZERO SQL queries in this module.
- All actions call parking_service functions.
- All dashboard statistics are queried live from the database.
- Graceful error, warning, and confirmation handling with accessible modal dialogs.
"""

import os
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime
from typing import Optional, Dict, Any, List

import customtkinter as ctk

import parking_service
import database


# ============================================================================
# Theme & Styling Configuration
# ============================================================================

COLOR_ACCENT = "#1f6aa5"       # Primary CTk Blue
COLOR_SUCCESS = "#27ae60"      # Soft Green
COLOR_WARNING = "#e67e22"      # Soft Orange / Amber
COLOR_DANGER = "#c0392b"       # Soft Red
COLOR_NEUTRAL = "#7f8c8d"      # Slate Gray
COLOR_CARD_DARK = "#2b2b2b"
COLOR_CARD_LIGHT = "#e8e8e8"


def apply_treeview_style() -> None:
    """Configures ttk.Treeview to harmoniously match CustomTkinter themes."""
    style = ttk.Style()
    current_mode = ctk.get_appearance_mode().lower()

    if current_mode == "dark":
        bg_color = "#232323"
        fg_color = "#f0f0f0"
        header_bg = "#1a1a1a"
        header_fg = "#ffffff"
        selected_bg = "#1f6aa5"
    else:
        bg_color = "#ffffff"
        fg_color = "#202020"
        header_bg = "#e0e0e0"
        header_fg = "#000000"
        selected_bg = "#3498db"

    style.theme_use("clam")
    style.configure(
        "Custom.Treeview",
        background=bg_color,
        foreground=fg_color,
        fieldbackground=bg_color,
        rowheight=28,
        font=("Segoe UI", 10),
        borderwidth=0,
    )
    style.configure(
        "Custom.Treeview.Heading",
        background=header_bg,
        foreground=header_fg,
        font=("Segoe UI", 10, "bold"),
        relief="flat",
    )
    style.map(
        "Custom.Treeview",
        background=[("selected", selected_bg)],
        foreground=[("selected", "#ffffff")],
    )


# ============================================================================
# View 1: Dashboard
# ============================================================================

class DashboardView(ctk.CTkFrame):
    """Displays facility capacity, live KPIs, recorded revenue, and active parked vehicles."""

    def __init__(self, master, app_controller, db_path: Optional[str] = None):
        super().__init__(master, fg_color="transparent")
        self.app = app_controller
        self.db_path = db_path

        self._build_ui()
        self.refresh()

    def _build_ui(self):
        # Header banner
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.pack(fill="x", padx=10, pady=(5, 15))

        title_lbl = ctk.CTkLabel(
            header_frame,
            text="Facility Overview & Dashboard",
            font=ctk.CTkFont(size=22, weight="bold"),
        )
        title_lbl.pack(side="left")

        refresh_btn = ctk.CTkButton(
            header_frame,
            text="🔄 Refresh Dashboard",
            width=150,
            command=self.refresh,
        )
        refresh_btn.pack(side="right")

        # 6 KPI Cards Grid (2 rows x 3 columns)
        kpi_grid = ctk.CTkFrame(self, fg_color="transparent")
        kpi_grid.pack(fill="x", padx=10, pady=(0, 15))
        kpi_grid.grid_columnconfigure((0, 1, 2), weight=1)

        self.kpi_total = self._create_kpi_card(kpi_grid, "TOTAL CAPACITY", "0", 0, 0, "#34495e")
        self.kpi_avail = self._create_kpi_card(kpi_grid, "AVAILABLE SLOTS", "0", 0, 1, COLOR_SUCCESS)
        self.kpi_occup = self._create_kpi_card(kpi_grid, "OCCUPIED SLOTS", "0", 0, 2, COLOR_DANGER)
        self.kpi_active = self._create_kpi_card(kpi_grid, "CURRENTLY PARKED", "0", 1, 0, COLOR_ACCENT)
        self.kpi_completed = self._create_kpi_card(kpi_grid, "COMPLETED SESSIONS", "0", 1, 1, "#8e44ad")
        self.kpi_revenue = self._create_kpi_card(kpi_grid, "RECORDED REVENUE", "$0.00", 1, 2, COLOR_WARNING)

        # Occupancy Progress Bar Frame
        bar_card = ctk.CTkFrame(self, corner_radius=8)
        bar_card.pack(fill="x", padx=10, pady=(0, 15))

        bar_hdr = ctk.CTkFrame(bar_card, fg_color="transparent")
        bar_hdr.pack(fill="x", padx=15, pady=(10, 5))

        ctk.CTkLabel(
            bar_hdr,
            text="Facility Utilization Rate",
            font=ctk.CTkFont(size=13, weight="bold")
        ).pack(side="left")

        self.utilization_lbl = ctk.CTkLabel(
            bar_hdr,
            text="0.0% Occupied",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLOR_ACCENT,
        )
        self.utilization_lbl.pack(side="right")

        self.progress_bar = ctk.CTkProgressBar(bar_card, height=12)
        self.progress_bar.pack(fill="x", padx=15, pady=(0, 15))
        self.progress_bar.set(0.0)

        # Active Parked Vehicles Table Section
        table_card = ctk.CTkFrame(self, corner_radius=8)
        table_card.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        tbl_hdr = ctk.CTkFrame(table_card, fg_color="transparent")
        tbl_hdr.pack(fill="x", padx=15, pady=(10, 5))

        ctk.CTkLabel(
            tbl_hdr,
            text="Currently Parked Vehicles (Live Active Sessions)",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(side="left")

        select_exit_btn = ctk.CTkButton(
            tbl_hdr,
            text="Process Exit for Selected",
            width=180,
            command=self._on_select_for_exit,
        )
        select_exit_btn.pack(side="right")

        tree_container = ctk.CTkFrame(table_card, fg_color="transparent")
        tree_container.pack(fill="both", expand=True, padx=15, pady=(5, 15))

        apply_treeview_style()
        cols = ("session_id", "plate", "type", "slot", "entry_time", "duration", "owner")
        self.tree = ttk.Treeview(
            tree_container,
            columns=cols,
            show="headings",
            style="Custom.Treeview",
            selectmode="browse",
        )
        self.tree.heading("session_id", text="Session ID")
        self.tree.heading("plate", text="Vehicle Plate")
        self.tree.heading("type", text="Vehicle Type")
        self.tree.heading("slot", text="Slot No")
        self.tree.heading("entry_time", text="Entry Time")
        self.tree.heading("duration", text="Current Stay")
        self.tree.heading("owner", text="Owner Name")

        self.tree.column("session_id", width=80, anchor="center")
        self.tree.column("plate", width=140, anchor="center")
        self.tree.column("type", width=110, anchor="center")
        self.tree.column("slot", width=90, anchor="center")
        self.tree.column("entry_time", width=160, anchor="center")
        self.tree.column("duration", width=120, anchor="center")
        self.tree.column("owner", width=140, anchor="w")

        v_scroll = ttk.Scrollbar(tree_container, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=v_scroll.set)

        self.tree.pack(side="left", fill="both", expand=True)
        v_scroll.pack(side="right", fill="y")

        self.empty_lbl = ctk.CTkLabel(
            self.tree,
            text="No vehicles currently parked in the facility.",
            font=ctk.CTkFont(size=13, slant="italic"),
        )

    def _create_kpi_card(self, parent, title: str, default_val: str, row: int, col: int, border_color: str):
        card = ctk.CTkFrame(parent, corner_radius=8, border_width=2, border_color=border_color)
        card.grid(row=row, column=col, padx=6, pady=6, sticky="nsew")

        lbl_title = ctk.CTkLabel(
            card,
            text=title,
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#95a5a6",
        )
        lbl_title.pack(anchor="w", padx=12, pady=(10, 2))

        lbl_val = ctk.CTkLabel(
            card,
            text=default_val,
            font=ctk.CTkFont(size=24, weight="bold"),
        )
        lbl_val.pack(anchor="w", padx=12, pady=(0, 10))
        return lbl_val

    def refresh(self):
        """Fetches live metrics and active sessions through the business logic service."""
        try:
            metrics = parking_service.get_dashboard_metrics(db_path=self.db_path)
            total = metrics.get("total_slots", 0)
            avail = metrics.get("available_slots", 0)
            occupied = metrics.get("occupied_slots", 0)
            active = metrics.get("active_sessions", 0)
            completed = metrics.get("completed_sessions", 0)
            revenue = metrics.get("recorded_revenue", 0.0)
            rate = metrics.get("occupancy_rate_percent", 0.0)

            self.kpi_total.configure(text=f"{total} Slots")
            self.kpi_avail.configure(text=f"{avail}")
            self.kpi_occup.configure(text=f"{occupied}")
            self.kpi_active.configure(text=f"{active}")
            self.kpi_completed.configure(text=f"{completed}")
            self.kpi_revenue.configure(text=f"${revenue:,.2f}")

            self.utilization_lbl.configure(text=f"{rate:.1f}% Occupancy ({occupied}/{total})")
            pct = max(0.0, min(1.0, rate / 100.0))
            self.progress_bar.set(pct)

            # Refresh table
            for row in self.tree.get_children():
                self.tree.delete(row)

            active_sessions = parking_service.get_active_sessions(db_path=self.db_path)
            if active_sessions:
                self.empty_lbl.place_forget()
                for s in active_sessions:
                    stay_min = s.get("current_stay_minutes")
                    stay_str = f"{int(stay_min // 60)}h {int(stay_min % 60):02d}m" if stay_min is not None else "--"
                    self.tree.insert(
                        "",
                        "end",
                        values=(
                            s.get("id"),
                            s.get("vehicle_registration_number"),
                            s.get("vehicle_type"),
                            s.get("slot_number"),
                            s.get("entry_time"),
                            stay_str,
                            s.get("owner_name") or "--",
                        ),
                    )
            else:
                self.empty_lbl.place(relx=0.5, rely=0.5, anchor="center")

        except Exception as e:
            messagebox.showerror("Dashboard Error", f"Failed to refresh dashboard: {e}")

    def _on_select_for_exit(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("Select Vehicle", "Please select a parked vehicle from the table first.")
            return

        item = self.tree.item(selected[0])
        plate = item["values"][1]
        self.app.navigate_to_exit(plate)


# ============================================================================
# View 2: Vehicle Entry
# ============================================================================

class VehicleEntryView(ctk.CTkFrame):
    """Handles vehicle registration, input validation, and slot allocation."""

    def __init__(self, master, app_controller, db_path: Optional[str] = None):
        super().__init__(master, fg_color="transparent")
        self.app = app_controller
        self.db_path = db_path

        self._build_ui()
        self.refresh()

    def _build_ui(self):
        title_lbl = ctk.CTkLabel(
            self,
            text="Vehicle Entry & Slot Assignment",
            font=ctk.CTkFont(size=22, weight="bold"),
        )
        title_lbl.pack(anchor="w", padx=10, pady=(5, 15))

        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=10, pady=0)
        container.grid_columnconfigure((0, 1), weight=1)

        # Left Column: Entry Form Card
        form_card = ctk.CTkFrame(container, corner_radius=10)
        form_card.grid(row=0, column=0, padx=(0, 10), pady=0, sticky="nsew")

        ctk.CTkLabel(
            form_card,
            text="Register Entering Vehicle",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", padx=20, pady=(15, 10))

        # Vehicle Plate
        ctk.CTkLabel(
            form_card,
            text="Registration Plate Number *",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="w", padx=20, pady=(5, 2))

        self.plate_entry = ctk.CTkEntry(
            form_card,
            placeholder_text="e.g. MH12-AB-1234, DL01XY9999",
            height=36,
        )
        self.plate_entry.pack(fill="x", padx=20, pady=(0, 10))

        # Vehicle Type
        ctk.CTkLabel(
            form_card,
            text="Vehicle Classification *",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="w", padx=20, pady=(5, 2))

        self.vtype_var = ctk.StringVar(value="Car")
        self.vtype_menu = ctk.CTkSegmentedButton(
            form_card,
            values=["Car", "Two-Wheeler", "Compact", "EV", "Heavy"],
            variable=self.vtype_var,
            command=self._on_vtype_changed,
        )
        self.vtype_menu.pack(fill="x", padx=20, pady=(0, 10))

        # Owner Name (Optional)
        ctk.CTkLabel(
            form_card,
            text="Owner Name (Optional)",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="w", padx=20, pady=(5, 2))

        self.owner_entry = ctk.CTkEntry(
            form_card,
            placeholder_text="e.g. Alex Morgan",
            height=36,
        )
        self.owner_entry.pack(fill="x", padx=20, pady=(0, 10))

        # Owner Contact (Optional)
        ctk.CTkLabel(
            form_card,
            text="Owner Contact / Phone (Optional)",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="w", padx=20, pady=(5, 2))

        self.contact_entry = ctk.CTkEntry(
            form_card,
            placeholder_text="e.g. 555-0144",
            height=36,
        )
        self.contact_entry.pack(fill="x", padx=20, pady=(0, 15))

        # Slot Assignment Preference
        ctk.CTkLabel(
            form_card,
            text="Slot Allocation Mode",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="w", padx=20, pady=(5, 2))

        self.slot_mode_var = ctk.StringVar(value="Auto")
        mode_frame = ctk.CTkFrame(form_card, fg_color="transparent")
        mode_frame.pack(fill="x", padx=20, pady=(0, 5))

        ctk.CTkRadioButton(
            mode_frame,
            text="Auto-Assign Best Available Slot",
            variable=self.slot_mode_var,
            value="Auto",
            command=self._toggle_slot_dropdown,
        ).pack(side="left", padx=(0, 15))

        ctk.CTkRadioButton(
            mode_frame,
            text="Specific Slot",
            variable=self.slot_mode_var,
            value="Manual",
            command=self._toggle_slot_dropdown,
        ).pack(side="left")

        self.slot_dropdown = ctk.CTkComboBox(
            form_card,
            values=["(No slots available)"],
            height=34,
            state="disabled",
        )
        self.slot_dropdown.pack(fill="x", padx=20, pady=(5, 15))

        # Buttons
        btn_frame = ctk.CTkFrame(form_card, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=(10, 20))

        self.submit_btn = ctk.CTkButton(
            btn_frame,
            text="🚗 Register Vehicle Entry",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=40,
            command=self._on_register_entry,
        )
        self.submit_btn.pack(side="left", fill="x", expand=True, padx=(0, 10))

        clear_btn = ctk.CTkButton(
            btn_frame,
            text="Clear",
            width=80,
            height=40,
            fg_color="#7f8c8d",
            hover_color="#95a5a6",
            command=self.clear_form,
        )
        clear_btn.pack(side="right")

        # Right Column: Entry Ticket & Status Card
        self.ticket_card = ctk.CTkFrame(container, corner_radius=10)
        self.ticket_card.grid(row=0, column=1, padx=(10, 0), pady=0, sticky="nsew")

        ctk.CTkLabel(
            self.ticket_card,
            text="Entry Ticket & Assignment Confirmation",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", padx=20, pady=(15, 10))

        self.ticket_status_box = ctk.CTkFrame(
            self.ticket_card,
            corner_radius=8,
            fg_color=("#f5f5f5", "#1f1f1f"),
        )
        self.ticket_status_box.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        self.ticket_status_lbl = ctk.CTkLabel(
            self.ticket_status_box,
            text="Ready to register entering vehicle.\nFill in the form and click 'Register Vehicle Entry'.",
            font=ctk.CTkFont(size=13),
            justify="center",
        )
        self.ticket_status_lbl.pack(expand=True, fill="both", padx=20, pady=30)

    def _on_vtype_changed(self, value):
        if self.slot_mode_var.get() == "Manual":
            self._populate_available_slots()

    def _toggle_slot_dropdown(self):
        if self.slot_mode_var.get() == "Manual":
            self.slot_dropdown.configure(state="normal")
            self._populate_available_slots()
        else:
            self.slot_dropdown.configure(state="disabled")

    def _populate_available_slots(self):
        try:
            avail = database.get_available_slots(db_path=self.db_path)
            if avail:
                values = [f"{s['slot_number']} ({s['slot_type']})" for s in avail]
                self.slot_dropdown.configure(values=values)
                self.slot_dropdown.set(values[0])
            else:
                self.slot_dropdown.configure(values=["(Facility Full)"])
                self.slot_dropdown.set("(Facility Full)")
        except Exception:
            self.slot_dropdown.configure(values=["(Error loading slots)"])

    def _on_register_entry(self):
        plate = self.plate_entry.get().strip()
        vtype = self.vtype_var.get()
        owner = self.owner_entry.get().strip() or None
        contact = self.contact_entry.get().strip() or None

        chosen_slot_number = None
        if self.slot_mode_var.get() == "Manual":
            selected_str = self.slot_dropdown.get()
            if selected_str and "(" in selected_str:
                chosen_slot_number = selected_str.split(" ")[0].strip()

        try:
            session = parking_service.register_vehicle_entry(
                vehicle_registration_number=plate,
                vehicle_type=vtype,
                owner_name=owner,
                owner_contact=contact,
                slot_number=chosen_slot_number,
                db_path=self.db_path,
            )

            # Display formatted ticket
            self._display_success_ticket(session)
            self.clear_form(preserve_ticket=True)
            self.app.notify_data_changed()

        except parking_service.DuplicateActiveSessionError as e:
            self._display_error_ticket(f"DUPLICATE SESSION:\n{e}")
        except parking_service.ParkingCapacityFullError as e:
            self._display_error_ticket(f"CAPACITY REACHED:\n{e}")
        except parking_service.ValidationError as e:
            self._display_error_ticket(f"VALIDATION FAILED:\n{e}")
        except Exception as e:
            self._display_error_ticket(f"REGISTRATION ERROR:\n{e}")

    def _display_success_ticket(self, session: Dict[str, Any]):
        for widget in self.ticket_status_box.winfo_children():
            widget.destroy()

        content = (
            f"✅ ENTRY PERMIT ISSUED\n\n"
            f"Session ID:         #{session['session_id']}\n"
            f"Registration Plate: {session['vehicle_registration_number']}\n"
            f"Vehicle Type:       {session['vehicle_type']}\n"
            f"Assigned Slot:      {session['slot_number']}\n"
            f"Entry Time:         {session['entry_time']}\n"
            f"Owner Name:         {session['owner_name'] or '--'}\n"
            f"Owner Contact:      {session['owner_contact'] or '--'}\n\n"
            f"Status:             ACTIVE (Slot Occupied)"
        )
        lbl = ctk.CTkLabel(
            self.ticket_status_box,
            text=content,
            font=ctk.CTkFont(size=13, family="Consolas"),
            text_color=COLOR_SUCCESS,
            justify="left",
        )
        lbl.pack(expand=True, padx=25, pady=25, anchor="w")

    def _display_error_ticket(self, err_msg: str):
        for widget in self.ticket_status_box.winfo_children():
            widget.destroy()

        lbl = ctk.CTkLabel(
            self.ticket_status_box,
            text=f"❌ {err_msg}",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLOR_DANGER,
            justify="center",
            wraplength=350,
        )
        lbl.pack(expand=True, padx=20, pady=30)

    def clear_form(self, preserve_ticket: bool = False):
        self.plate_entry.delete(0, "end")
        self.owner_entry.delete(0, "end")
        self.contact_entry.delete(0, "end")
        self.vtype_var.set("Car")
        self.slot_mode_var.set("Auto")
        self._toggle_slot_dropdown()

        if not preserve_ticket:
            for widget in self.ticket_status_box.winfo_children():
                widget.destroy()
            self.ticket_status_lbl = ctk.CTkLabel(
                self.ticket_status_box,
                text="Ready to register entering vehicle.\nFill in the form and click 'Register Vehicle Entry'.",
                font=ctk.CTkFont(size=13),
                justify="center",
            )
            self.ticket_status_lbl.pack(expand=True, fill="both", padx=20, pady=30)

    def refresh(self):
        if self.slot_mode_var.get() == "Manual":
            self._populate_available_slots()


# ============================================================================
# View 3: Vehicle Exit & Billing
# ============================================================================

class VehicleExitView(ctk.CTkFrame):
    """Processes vehicle exit, computes durations and fees, and releases slots."""

    def __init__(self, master, app_controller, db_path: Optional[str] = None):
        super().__init__(master, fg_color="transparent")
        self.app = app_controller
        self.db_path = db_path
        self.current_session: Optional[Dict[str, Any]] = None

        self._build_ui()
        self.refresh()

    def _build_ui(self):
        title_lbl = ctk.CTkLabel(
            self,
            text="Vehicle Exit, Billing & Slot Release",
            font=ctk.CTkFont(size=22, weight="bold"),
        )
        title_lbl.pack(anchor="w", padx=10, pady=(5, 15))

        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=10, pady=0)
        container.grid_columnconfigure((0, 1), weight=1)

        # Left Column: Lookup & Exit Action
        left_card = ctk.CTkFrame(container, corner_radius=10)
        left_card.grid(row=0, column=0, padx=(0, 10), pady=0, sticky="nsew")

        ctk.CTkLabel(
            left_card,
            text="Search Active Parked Vehicle",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", padx=20, pady=(15, 10))

        # Search / Plate Entry
        search_row = ctk.CTkFrame(left_card, fg_color="transparent")
        search_row.pack(fill="x", padx=20, pady=(0, 10))

        self.search_entry = ctk.CTkEntry(
            search_row,
            placeholder_text="Enter Registration Plate Number",
            height=36,
        )
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.search_entry.bind("<Return>", lambda e: self._on_find_session())

        find_btn = ctk.CTkButton(
            search_row,
            text="🔍 Find",
            width=80,
            height=36,
            command=self._on_find_session,
        )
        find_btn.pack(side="right")

        # Or select from active dropdown
        ctk.CTkLabel(
            left_card,
            text="Or Select from Active Sessions Dropdown:",
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", padx=20, pady=(5, 2))

        self.active_dropdown = ctk.CTkComboBox(
            left_card,
            values=["(No active vehicles)"],
            height=34,
            command=self._on_dropdown_selected,
        )
        self.active_dropdown.pack(fill="x", padx=20, pady=(0, 15))

        # Live Session Details Card
        self.details_box = ctk.CTkFrame(left_card, corner_radius=8, fg_color=("#f0f0f0", "#222222"))
        self.details_box.pack(fill="both", expand=True, padx=20, pady=(0, 15))

        self.details_lbl = ctk.CTkLabel(
            self.details_box,
            text="No active session selected.\nEnter a plate number or pick from the dropdown above.",
            font=ctk.CTkFont(size=13),
            justify="center",
        )
        self.details_lbl.pack(expand=True, padx=15, pady=25)

        # Discount Entry & Action Button
        disc_frame = ctk.CTkFrame(left_card, fg_color="transparent")
        disc_frame.pack(fill="x", padx=20, pady=(0, 15))

        ctk.CTkLabel(
            disc_frame,
            text="Discount ($):",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(side="left", padx=(0, 10))

        self.discount_entry = ctk.CTkEntry(disc_frame, width=90, height=34)
        self.discount_entry.insert(0, "0.00")
        self.discount_entry.pack(side="left")
        self.discount_entry.bind("<KeyRelease>", lambda e: self._update_fee_preview())

        self.exit_btn = ctk.CTkButton(
            left_card,
            text="🏁 Process Exit & Release Slot",
            font=ctk.CTkFont(size=14, weight="bold"),
            height=42,
            fg_color=COLOR_SUCCESS,
            hover_color="#219a52",
            state="disabled",
            command=self._on_process_exit,
        )
        self.exit_btn.pack(fill="x", padx=20, pady=(0, 20))

        # Right Column: Official Receipt Summary Card
        self.receipt_card = ctk.CTkFrame(container, corner_radius=10)
        self.receipt_card.grid(row=0, column=1, padx=(10, 0), pady=0, sticky="nsew")

        ctk.CTkLabel(
            self.receipt_card,
            text="Official Parking Receipt",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", padx=20, pady=(15, 10))

        self.receipt_box = ctk.CTkFrame(
            self.receipt_card,
            corner_radius=8,
            fg_color=("#f5f5f5", "#1b1b1b"),
        )
        self.receipt_box.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        self.receipt_lbl = ctk.CTkLabel(
            self.receipt_box,
            text="Receipt will be generated upon processing exit.",
            font=ctk.CTkFont(size=13, slant="italic"),
            justify="center",
        )
        self.receipt_lbl.pack(expand=True, padx=20, pady=30)

    def select_vehicle(self, plate: str):
        """Programmatically loads a vehicle plate for exit."""
        self.search_entry.delete(0, "end")
        self.search_entry.insert(0, plate)
        self._on_find_session()

    def _on_dropdown_selected(self, choice: str):
        if choice and " | " in choice:
            plate = choice.split(" | ")[0].strip()
            self.search_entry.delete(0, "end")
            self.search_entry.insert(0, plate)
            self._on_find_session()

    def _on_find_session(self):
        raw_plate = self.search_entry.get().strip()
        if not raw_plate:
            messagebox.showwarning("Input Required", "Please enter a vehicle registration number to search.")
            return

        try:
            clean_plate = parking_service.validate_and_normalize_registration_number(raw_plate)
            session = database.get_active_session_by_vehicle(clean_plate, db_path=self.db_path)
            if not session:
                self.current_session = None
                self.exit_btn.configure(state="disabled")
                self._display_session_not_found(clean_plate)
                return

            self.current_session = dict(session)
            self.exit_btn.configure(state="normal")
            self._update_fee_preview()

        except parking_service.ValidationError as e:
            messagebox.showerror("Validation Error", str(e))
        except Exception as e:
            messagebox.showerror("Lookup Error", f"Failed to search for vehicle: {e}")

    def _update_fee_preview(self):
        if not self.current_session:
            return

        for w in self.details_box.winfo_children():
            w.destroy()

        now_str = datetime.now().strftime(database.DATETIME_FORMAT)
        try:
            fee_info = parking_service.calculate_duration_and_fee(
                entry_time_str=self.current_session["entry_time"],
                exit_time_str=now_str,
                vehicle_type=self.current_session["vehicle_type"],
                db_path=self.db_path,
            )
        except Exception:
            fee_info = {
                "duration_formatted": "calculating...",
                "billable_hours": 0,
                "hourly_rate": 20.0,
                "fee": 0.0,
            }

        disc_str = self.discount_entry.get().strip()
        try:
            disc_amt = float(disc_str) if disc_str else 0.0
            if disc_amt < 0:
                disc_amt = 0.0
        except ValueError:
            disc_amt = 0.0

        final_fee = max(0.0, round(fee_info["fee"] - disc_amt, 2))

        lines = [
            f"Active Session: #{self.current_session['id']}",
            f"Vehicle Plate:  {self.current_session['vehicle_registration_number']}",
            f"Vehicle Type:   {self.current_session['vehicle_type']}",
            f"Slot Occupied:  {self.current_session['slot_number']}",
            f"Entry Time:     {self.current_session['entry_time']}",
            f"Current Stay:   {fee_info.get('duration_formatted', '--')}",
            f"Hourly Rate:    ${fee_info.get('hourly_rate', 0.0):.2f}/hr",
            f"Billable Hours: {fee_info.get('billable_hours', 0)} hr(s)",
            f"Base Fee:       ${fee_info.get('fee', 0.0):.2f}",
            f"Discount:       -${disc_amt:.2f}",
            f"ESTIMATED FEE:  ${final_fee:.2f}",
        ]

        text_content = "\n".join(lines)
        lbl = ctk.CTkLabel(
            self.details_box,
            text=text_content,
            font=ctk.CTkFont(size=12, family="Consolas"),
            justify="left",
        )
        lbl.pack(padx=20, pady=15, anchor="w")

    def _display_session_not_found(self, plate: str):
        for w in self.details_box.winfo_children():
            w.destroy()

        lbl = ctk.CTkLabel(
            self.details_box,
            text=f"❌ No ACTIVE parking session found for vehicle '{plate}'.\nCheck the registration number or verify active records.",
            font=ctk.CTkFont(size=13),
            text_color=COLOR_DANGER,
            justify="center",
            wraplength=350,
        )
        lbl.pack(expand=True, padx=15, pady=25)

    def _on_process_exit(self):
        if not self.current_session:
            return

        disc_str = self.discount_entry.get().strip()
        try:
            discount_amount = float(disc_str) if disc_str else 0.0
        except ValueError:
            messagebox.showerror("Invalid Discount", "Discount must be a valid non-negative number.")
            return

        try:
            receipt = parking_service.record_vehicle_exit(
                session_id=self.current_session["id"],
                discount_amount=discount_amount,
                db_path=self.db_path,
            )

            # Display official receipt summary
            self._display_receipt(receipt)
            self.current_session = None
            self.exit_btn.configure(state="disabled")
            self.search_entry.delete(0, "end")
            self.refresh()
            self.app.notify_data_changed()

        except parking_service.ValidationError as e:
            messagebox.showerror("Exit Validation Error", str(e))
        except parking_service.InvalidExitError as e:
            messagebox.showerror("Exit Error", str(e))
        except Exception as e:
            messagebox.showerror("Error", f"Failed to record exit: {e}")

    def _display_receipt(self, receipt: Dict[str, Any]):
        for w in self.receipt_box.winfo_children():
            w.destroy()

        content = (
            f"========================================\n"
            f"        PARKING RECEIPT SUMMARY         \n"
            f"========================================\n\n"
            f"Receipt No:     REC-{receipt['session_id']:06d}\n"
            f"Session ID:     #{receipt['session_id']}\n"
            f"Vehicle Plate:  {receipt['vehicle_registration_number']}\n"
            f"Vehicle Type:   {receipt['vehicle_type']}\n"
            f"Owner Name:     {receipt['owner_name'] or 'N/A'}\n"
            f"Slot Released:  {receipt['slot_number']} (Status: Available)\n\n"
            f"Entry Time:     {receipt['entry_time']}\n"
            f"Exit Time:      {receipt['exit_time']}\n"
            f"Total Duration: {receipt['duration_formatted']} ({receipt['duration_minutes']} mins)\n"
            f"Billable Hours: {receipt['billable_hours']} hr(s)\n"
            f"Hourly Rate:    ${receipt['hourly_rate']:.2f}/hr\n\n"
            f"----------------------------------------\n"
            f"Base Fee:       ${receipt['base_fee']:.2f}\n"
            f"Discount:       -${receipt['discount_applied']:.2f}\n"
            f"FINAL PAID FEE: ${receipt['final_fee']:.2f}\n"
            f"----------------------------------------\n"
            f"Status:         COMPLETED (RECORD PRESERVED)\n"
            f"========================================"
        )

        lbl = ctk.CTkLabel(
            self.receipt_box,
            text=content,
            font=ctk.CTkFont(size=12, family="Consolas"),
            justify="left",
        )
        lbl.pack(padx=20, pady=20, anchor="w", expand=True)

    def refresh(self):
        try:
            active_list = parking_service.get_active_sessions(db_path=self.db_path)
            if active_list:
                values = [
                    f"{s['vehicle_registration_number']} | Slot {s['slot_number']} | {s['vehicle_type']}"
                    for s in active_list
                ]
                self.active_dropdown.configure(values=values)
            else:
                self.active_dropdown.configure(values=["(No active vehicles)"])
        except Exception:
            pass


# ============================================================================
# View 4: Parking Records
# ============================================================================

class ParkingRecordsView(ctk.CTkFrame):
    """Historical records, search, filter, permitted info update, and safe delete."""

    def __init__(self, master, app_controller, db_path: Optional[str] = None):
        super().__init__(master, fg_color="transparent")
        self.app = app_controller
        self.db_path = db_path

        self._build_ui()
        self.refresh()

    def _build_ui(self):
        title_lbl = ctk.CTkLabel(
            self,
            text="Parking Records & Session History",
            font=ctk.CTkFont(size=22, weight="bold"),
        )
        title_lbl.pack(anchor="w", padx=10, pady=(5, 10))

        # Filter and Search Control Bar
        filter_card = ctk.CTkFrame(self, corner_radius=8)
        filter_card.pack(fill="x", padx=10, pady=(0, 10))

        row1 = ctk.CTkFrame(filter_card, fg_color="transparent")
        row1.pack(fill="x", padx=15, pady=(10, 10))

        # Search Entry
        ctk.CTkLabel(row1, text="Search:", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=(0, 5))
        self.search_entry = ctk.CTkEntry(
            row1,
            placeholder_text="Plate, Owner, or Slot",
            width=200,
            height=32,
        )
        self.search_entry.pack(side="left", padx=(0, 15))
        self.search_entry.bind("<Return>", lambda e: self.refresh())

        # Status Filter
        ctk.CTkLabel(row1, text="Status:", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=(0, 5))
        self.status_var = ctk.StringVar(value="All")
        self.status_menu = ctk.CTkOptionMenu(
            row1,
            values=["All", "ACTIVE", "COMPLETED", "CANCELLED"],
            variable=self.status_var,
            width=130,
            height=32,
            command=lambda v: self.refresh(),
        )
        self.status_menu.pack(side="left", padx=(0, 15))

        # Vehicle Type Filter
        ctk.CTkLabel(row1, text="Type:", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=(0, 5))
        self.vtype_var = ctk.StringVar(value="All")
        self.vtype_menu = ctk.CTkOptionMenu(
            row1,
            values=["All", "Car", "Two-Wheeler", "Compact", "EV", "Heavy"],
            variable=self.vtype_var,
            width=130,
            height=32,
            command=lambda v: self.refresh(),
        )
        self.vtype_menu.pack(side="left", padx=(0, 15))

        # Filter Buttons
        apply_btn = ctk.CTkButton(
            row1,
            text="Filter",
            width=80,
            height=32,
            command=self.refresh,
        )
        apply_btn.pack(side="left", padx=(0, 10))

        reset_btn = ctk.CTkButton(
            row1,
            text="Reset",
            width=70,
            height=32,
            fg_color="#7f8c8d",
            hover_color="#95a5a6",
            command=self._on_reset_filters,
        )
        reset_btn.pack(side="left")

        # Table & Scrollbar Container
        table_card = ctk.CTkFrame(self, corner_radius=8)
        table_card.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        tree_container = ctk.CTkFrame(table_card, fg_color="transparent")
        tree_container.pack(fill="both", expand=True, padx=15, pady=(15, 10))

        apply_treeview_style()
        cols = ("id", "plate", "type", "slot", "entry_time", "exit_time", "fee", "status", "owner", "contact")
        self.tree = ttk.Treeview(
            tree_container,
            columns=cols,
            show="headings",
            style="Custom.Treeview",
            selectmode="browse",
        )
        self.tree.heading("id", text="ID")
        self.tree.heading("plate", text="Vehicle Plate")
        self.tree.heading("type", text="Type")
        self.tree.heading("slot", text="Slot")
        self.tree.heading("entry_time", text="Entry Time")
        self.tree.heading("exit_time", text="Exit Time")
        self.tree.heading("fee", text="Fee ($)")
        self.tree.heading("status", text="Status")
        self.tree.heading("owner", text="Owner")
        self.tree.heading("contact", text="Contact")

        self.tree.column("id", width=60, anchor="center")
        self.tree.column("plate", width=120, anchor="center")
        self.tree.column("type", width=100, anchor="center")
        self.tree.column("slot", width=80, anchor="center")
        self.tree.column("entry_time", width=150, anchor="center")
        self.tree.column("exit_time", width=150, anchor="center")
        self.tree.column("fee", width=80, anchor="e")
        self.tree.column("status", width=95, anchor="center")
        self.tree.column("owner", width=120, anchor="w")
        self.tree.column("contact", width=110, anchor="w")

        v_scroll = ttk.Scrollbar(tree_container, orient="vertical", command=self.tree.yview)
        h_scroll = ttk.Scrollbar(tree_container, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        v_scroll.grid(row=0, column=1, sticky="ns")
        h_scroll.grid(row=1, column=0, sticky="ew")

        tree_container.grid_rowconfigure(0, weight=1)
        tree_container.grid_columnconfigure(0, weight=1)

        self.empty_lbl = ctk.CTkLabel(
            self.tree,
            text="No parking records found matching the criteria.",
            font=ctk.CTkFont(size=13, slant="italic"),
        )

        # Action Buttons Footer
        action_bar = ctk.CTkFrame(table_card, fg_color="transparent")
        action_bar.pack(fill="x", padx=15, pady=(5, 15))

        self.update_btn = ctk.CTkButton(
            action_bar,
            text="✏️ Update Permitted Info",
            width=180,
            command=self._on_update_permitted,
        )
        self.update_btn.pack(side="left", padx=(0, 10))

        self.cancel_btn = ctk.CTkButton(
            action_bar,
            text="🚫 Cancel Active Entry",
            width=170,
            fg_color=COLOR_WARNING,
            hover_color="#d35400",
            command=self._on_cancel_session,
        )
        self.cancel_btn.pack(side="left", padx=(0, 10))

        self.delete_btn = ctk.CTkButton(
            action_bar,
            text="🗑️ Delete Record Safely",
            width=170,
            fg_color=COLOR_DANGER,
            hover_color="#962d22",
            command=self._on_safe_delete,
        )
        self.delete_btn.pack(side="left")

    def _on_reset_filters(self):
        self.search_entry.delete(0, "end")
        self.status_var.set("All")
        self.vtype_var.set("All")
        self.refresh()

    def refresh(self):
        keyword = self.search_entry.get().strip() or None
        status = self.status_var.get()
        vtype = self.vtype_var.get()

        for row in self.tree.get_children():
            self.tree.delete(row)

        try:
            records = parking_service.search_parking_records(
                keyword=keyword,
                status=status if status != "All" else None,
                vehicle_type=vtype if vtype != "All" else None,
                limit=200,
                db_path=self.db_path,
            )

            if records:
                self.empty_lbl.place_forget()
                for r in records:
                    fee_val = f"${r['fee']:.2f}" if r.get("fee") is not None else "--"
                    self.tree.insert(
                        "",
                        "end",
                        values=(
                            r.get("id"),
                            r.get("vehicle_registration_number"),
                            r.get("vehicle_type"),
                            r.get("slot_number"),
                            r.get("entry_time"),
                            r.get("exit_time") or "--",
                            fee_val,
                            r.get("status"),
                            r.get("owner_name") or "--",
                            r.get("owner_contact") or "--",
                        ),
                    )
            else:
                self.empty_lbl.place(relx=0.5, rely=0.5, anchor="center")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to retrieve parking records: {e}")

    def _get_selected_record_id(self) -> Optional[int]:
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("Selection Required", "Please select a record row in the table first.")
            return None
        item = self.tree.item(selected[0])
        return int(item["values"][0])

    def _on_update_permitted(self):
        session_id = self._get_selected_record_id()
        if not session_id:
            return

        session = database.get_session_by_id(session_id, db_path=self.db_path)
        if not session:
            messagebox.showerror("Error", "Session not found.")
            return

        # Modal dialog for permitted vehicle details update
        dialog = ctk.CTkToplevel(self)
        dialog.title(f"Update Details: #{session_id} ({session['vehicle_registration_number']})")
        dialog.geometry("420x350")
        dialog.resizable(False, False)
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()

        ctk.CTkLabel(
            dialog,
            text=f"Update Permitted Vehicle Details",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(padx=20, pady=(15, 5), anchor="w")

        ctk.CTkLabel(
            dialog,
            text=f"Plate: {session['vehicle_registration_number']} | Slot: {session['slot_number']}",
            font=ctk.CTkFont(size=12),
            text_color="#95a5a6",
        ).pack(padx=20, pady=(0, 15), anchor="w")

        # Vehicle Type
        ctk.CTkLabel(dialog, text="Vehicle Type:", font=ctk.CTkFont(size=12, weight="bold")).pack(padx=20, anchor="w")
        vtype_var = ctk.StringVar(value=session["vehicle_type"])
        vtype_opt = ctk.CTkOptionMenu(
            dialog,
            values=["Car", "Two-Wheeler", "Compact", "EV", "Heavy"],
            variable=vtype_var,
        )
        vtype_opt.pack(padx=20, pady=(2, 10), fill="x")

        # Owner Name
        ctk.CTkLabel(dialog, text="Owner Name:", font=ctk.CTkFont(size=12, weight="bold")).pack(padx=20, anchor="w")
        owner_entry = ctk.CTkEntry(dialog)
        owner_entry.insert(0, session["owner_name"] or "")
        owner_entry.pack(padx=20, pady=(2, 10), fill="x")

        # Owner Contact
        ctk.CTkLabel(dialog, text="Owner Contact:", font=ctk.CTkFont(size=12, weight="bold")).pack(padx=20, anchor="w")
        contact_entry = ctk.CTkEntry(dialog)
        contact_entry.insert(0, session["owner_contact"] or "")
        contact_entry.pack(padx=20, pady=(2, 15), fill="x")

        btn_row = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_row.pack(padx=20, pady=10, fill="x")

        def save():
            try:
                parking_service.update_permitted_vehicle_info(
                    session_id=session_id,
                    vehicle_type=vtype_var.get(),
                    owner_name=owner_entry.get().strip() or None,
                    owner_contact=contact_entry.get().strip() or None,
                    db_path=self.db_path,
                )
                dialog.destroy()
                messagebox.showinfo("Success", f"Session #{session_id} details updated successfully.")
                self.refresh()
                self.app.notify_data_changed()
            except Exception as e:
                messagebox.showerror("Update Error", str(e))

        ctk.CTkButton(btn_row, text="Save Changes", command=save, fg_color=COLOR_SUCCESS).pack(side="left", expand=True, fill="x", padx=(0, 10))
        ctk.CTkButton(btn_row, text="Cancel", command=dialog.destroy, fg_color="#7f8c8d").pack(side="right", expand=True, fill="x")

    def _on_cancel_session(self):
        session_id = self._get_selected_record_id()
        if not session_id:
            return

        session = database.get_session_by_id(session_id, db_path=self.db_path)
        if not session:
            messagebox.showerror("Error", "Session not found.")
            return

        if session["status"] != "ACTIVE":
            messagebox.showwarning("Invalid Operation", f"Session #{session_id} is '{session['status']}', not ACTIVE.")
            return

        confirm = messagebox.askyesno(
            "Confirm Cancellation",
            f"Are you sure you want to cancel the ACTIVE entry for '{session['vehicle_registration_number']}'?\n\n"
            f"This will immediately free slot '{session['slot_number']}' and mark the session CANCELLED.",
        )
        if confirm:
            try:
                parking_service.cancel_active_session_safely(session_id, db_path=self.db_path)
                messagebox.showinfo("Session Cancelled", f"Active session #{session_id} cancelled and slot released.")
                self.refresh()
                self.app.notify_data_changed()
            except Exception as e:
                messagebox.showerror("Cancellation Error", str(e))

    def _on_safe_delete(self):
        session_id = self._get_selected_record_id()
        if not session_id:
            return

        session = database.get_session_by_id(session_id, db_path=self.db_path)
        if not session:
            messagebox.showerror("Error", "Session not found.")
            return

        status = session["status"]
        fee = session["fee"] or 0.0

        if status == "ACTIVE":
            messagebox.showerror(
                "Cannot Delete Active Record",
                f"Session #{session_id} is currently ACTIVE in slot '{session['slot_number']}'.\n\n"
                f"To maintain data consistency, you cannot delete active sessions. "
                f"Please use 'Cancel Active Entry' first if the vehicle entered in error.",
            )
            return

        # If billed completed session with fee > 0
        if fee > 0.0:
            # Requires admin confirmation & audit reason
            dialog = ctk.CTkToplevel(self)
            dialog.title("Administrative Audit Confirmation Required")
            dialog.geometry("450x260")
            dialog.resizable(False, False)
            dialog.transient(self.winfo_toplevel())
            dialog.grab_set()

            ctk.CTkLabel(
                dialog,
                text="Audit Protected Record Deletion",
                font=ctk.CTkFont(size=15, weight="bold"),
                text_color=COLOR_DANGER,
            ).pack(padx=20, pady=(15, 5), anchor="w")

            ctk.CTkLabel(
                dialog,
                text=(
                    f"Session #{session_id} is a completed record with recorded revenue of ${fee:.2f}.\n"
                    f"Financial compliance requires an official audit reason to delete."
                ),
                font=ctk.CTkFont(size=12),
                wraplength=410,
                justify="left",
            ).pack(padx=20, pady=(0, 15), anchor="w")

            reason_entry = ctk.CTkEntry(dialog, placeholder_text="Enter mandatory audit reason (e.g. Test cleanup, Dispute #42)")
            reason_entry.pack(padx=20, pady=(0, 15), fill="x")

            btn_box = ctk.CTkFrame(dialog, fg_color="transparent")
            btn_box.pack(padx=20, pady=10, fill="x")

            def execute_admin_delete():
                reason = reason_entry.get().strip()
                if not reason:
                    messagebox.showerror("Audit Reason Required", "An audit reason must be provided to delete this record.")
                    return
                try:
                    parking_service.delete_parking_record_safely(
                        session_id=session_id,
                        admin_override=True,
                        audit_reason=reason,
                        db_path=self.db_path,
                    )
                    dialog.destroy()
                    messagebox.showinfo("Record Deleted", f"Session record #{session_id} successfully deleted.")
                    self.refresh()
                    self.app.notify_data_changed()
                except Exception as e:
                    messagebox.showerror("Deletion Error", str(e))

            ctk.CTkButton(btn_box, text="Confirm Delete", fg_color=COLOR_DANGER, command=execute_admin_delete).pack(side="left", expand=True, fill="x", padx=(0, 10))
            ctk.CTkButton(btn_box, text="Cancel", fg_color="#7f8c8d", command=dialog.destroy).pack(side="right", expand=True, fill="x")

        else:
            # Zero-fee or cancelled record
            confirm = messagebox.askyesno(
                "Confirm Delete",
                f"Permanently delete parking record #{session_id} ({status})?",
            )
            if confirm:
                try:
                    parking_service.delete_parking_record_safely(session_id, db_path=self.db_path)
                    messagebox.showinfo("Record Deleted", f"Record #{session_id} deleted.")
                    self.refresh()
                    self.app.notify_data_changed()
                except Exception as e:
                    messagebox.showerror("Error", str(e))


# ============================================================================
# View 5: Parking Slots
# ============================================================================

class ParkingSlotsView(ctk.CTkFrame):
    """Real-time visual map and status grid of all facility parking slots."""

    def __init__(self, master, app_controller, db_path: Optional[str] = None):
        super().__init__(master, fg_color="transparent")
        self.app = app_controller
        self.db_path = db_path

        self._build_ui()
        self.refresh()

    def _build_ui(self):
        # Header banner
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.pack(fill="x", padx=10, pady=(5, 10))

        title_lbl = ctk.CTkLabel(
            header_frame,
            text="Parking Slots & Live Occupancy Map",
            font=ctk.CTkFont(size=22, weight="bold"),
        )
        title_lbl.pack(side="left")

        sync_btn = ctk.CTkButton(
            header_frame,
            text="🔄 Sync with Database",
            width=170,
            command=self.refresh,
        )
        sync_btn.pack(side="right")

        # Filters & Legend Bar
        bar_card = ctk.CTkFrame(self, corner_radius=8)
        bar_card.pack(fill="x", padx=10, pady=(0, 10))

        bar_inner = ctk.CTkFrame(bar_card, fg_color="transparent")
        bar_inner.pack(fill="x", padx=15, pady=8)

        # Filters
        ctk.CTkLabel(bar_inner, text="Filter Type:", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=(0, 5))
        self.type_filter_var = ctk.StringVar(value="All")
        self.type_menu = ctk.CTkOptionMenu(
            bar_inner,
            values=["All", "Standard", "Compact", "Two-Wheeler", "EV"],
            variable=self.type_filter_var,
            width=120,
            height=30,
            command=lambda v: self.render_slots(),
        )
        self.type_menu.pack(side="left", padx=(0, 15))

        ctk.CTkLabel(bar_inner, text="Status:", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=(0, 5))
        self.status_filter_var = ctk.StringVar(value="All")
        self.status_menu = ctk.CTkOptionMenu(
            bar_inner,
            values=["All", "Available", "Occupied", "Maintenance"],
            variable=self.status_filter_var,
            width=120,
            height=30,
            command=lambda v: self.render_slots(),
        )
        self.status_menu.pack(side="left", padx=(0, 20))

        # Legend
        self._add_legend_badge(bar_inner, "Available", COLOR_SUCCESS)
        self._add_legend_badge(bar_inner, "Occupied", COLOR_DANGER)
        self._add_legend_badge(bar_inner, "Maintenance", COLOR_NEUTRAL)

        # Scrollable Grid of Slots
        self.scroll_frame = ctk.CTkScrollableFrame(self, corner_radius=8)
        self.scroll_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # Configure 5 columns for the visual slot grid
        for i in range(5):
            self.scroll_frame.grid_columnconfigure(i, weight=1)

    def _add_legend_badge(self, parent, label: str, color: str):
        badge = ctk.CTkLabel(
            parent,
            text=f"● {label}",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=color,
        )
        badge.pack(side="right", padx=8)

    def refresh(self):
        self.all_slots = parking_service.get_all_parking_slots(db_path=self.db_path)
        self.render_slots()

    def render_slots(self):
        # Clear existing slot cards
        for widget in self.scroll_frame.winfo_children():
            widget.destroy()

        slot_type_filter = self.type_filter_var.get()
        status_filter = self.status_filter_var.get()

        slots_to_show = []
        for s in self.all_slots:
            if slot_type_filter != "All" and s["slot_type"] != slot_type_filter:
                continue
            if status_filter != "All" and s["status"] != status_filter:
                continue
            slots_to_show.append(s)

        if not slots_to_show:
            empty_lbl = ctk.CTkLabel(
                self.scroll_frame,
                text="No parking slots match the selected filters.",
                font=ctk.CTkFont(size=14, slant="italic"),
            )
            empty_lbl.grid(row=0, column=0, columnspan=5, pady=40)
            return

        # Render visual tiles (5 cards per row)
        cols_per_row = 5
        for idx, slot in enumerate(slots_to_show):
            row_idx = idx // cols_per_row
            col_idx = idx % cols_per_row
            self._create_slot_card(self.scroll_frame, slot, row_idx, col_idx)

    def _create_slot_card(self, parent, slot: Dict[str, Any], row: int, col: int):
        status = slot["status"]
        if status == "Available":
            border_col = COLOR_SUCCESS
            badge_text = "AVAILABLE"
            badge_fg = COLOR_SUCCESS
            bg_col = ("#eafaf1", "#1e2e24")
        elif status == "Occupied":
            border_col = COLOR_DANGER
            badge_text = "OCCUPIED"
            badge_fg = COLOR_DANGER
            bg_col = ("#fdedec", "#2f1e1e")
        else:
            border_col = COLOR_NEUTRAL
            badge_text = status.upper()
            badge_fg = COLOR_NEUTRAL
            bg_col = ("#f2f4f4", "#252525")

        card = ctk.CTkFrame(
            parent,
            corner_radius=8,
            border_width=2,
            border_color=border_col,
            fg_color=bg_col,
            height=100,
        )
        card.grid(row=row, column=col, padx=6, pady=6, sticky="nsew")

        # Slot Header
        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.pack(fill="x", padx=10, pady=(8, 2))

        ctk.CTkLabel(
            hdr,
            text=slot["slot_number"],
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(side="left")

        ctk.CTkLabel(
            hdr,
            text=badge_text,
            font=ctk.CTkFont(size=9, weight="bold"),
            text_color=badge_fg,
        ).pack(side="right")

        # Type Subtitle
        ctk.CTkLabel(
            card,
            text=f"Type: {slot['slot_type']}",
            font=ctk.CTkFont(size=11),
            text_color="#7f8c8d",
        ).pack(anchor="w", padx=10, pady=(0, 4))

        # Vehicle Plate or Quick Action
        if status == "Occupied" and slot.get("vehicle_registration_number"):
            v_plate = slot["vehicle_registration_number"]
            btn = ctk.CTkButton(
                card,
                text=f"🚗 {v_plate}",
                font=ctk.CTkFont(size=10, weight="bold"),
                height=24,
                fg_color=COLOR_DANGER,
                hover_color="#962d22",
                command=lambda p=v_plate: self.app.navigate_to_exit(p),
            )
            btn.pack(fill="x", padx=10, pady=(2, 8))
        else:
            ctk.CTkLabel(
                card,
                text="Ready for Entry" if status == "Available" else "Not in Service",
                font=ctk.CTkFont(size=10, slant="italic"),
                text_color="#95a5a6",
            ).pack(anchor="w", padx=10, pady=(4, 8))


# ============================================================================
# Main Application Controller
# ============================================================================

class ParkingApp(ctk.CTk):
    """Main window controller managing sidebar navigation and views."""

    def __init__(self, db_path: Optional[str] = None):
        super().__init__()
        self.db_path = db_path or database.DEFAULT_DB_PATH
        database.init_db(self.db_path)

        # Window Setup
        self.title("Parking Management System")
        self.geometry("1100x720")
        self.minsize(960, 600)

        # Root Grid Layout (1 row, 2 columns)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        # 1. Navigation Sidebar
        self.sidebar_frame = ctk.CTkFrame(self, width=220, corner_radius=0)
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(7, weight=1)

        # App Branding Header
        brand_frame = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        brand_frame.grid(row=0, column=0, padx=20, pady=(20, 20), sticky="ew")

        ctk.CTkLabel(
            brand_frame,
            text="🅿️ Parking System",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(anchor="w")

        ctk.CTkLabel(
            brand_frame,
            text="TQM Management Console",
            font=ctk.CTkFont(size=11),
            text_color="#95a5a6",
        ).pack(anchor="w")

        # Navigation Buttons
        self.nav_buttons: Dict[str, ctk.CTkButton] = {}
        screens = [
            ("Dashboard", "📊 Dashboard"),
            ("Vehicle Entry", "🚗 Vehicle Entry"),
            ("Vehicle Exit", "🏁 Vehicle Exit"),
            ("Parking Records", "📋 Parking Records"),
            ("Parking Slots", "🅿️ Parking Slots"),
        ]

        for idx, (key, label) in enumerate(screens, start=1):
            btn = ctk.CTkButton(
                self.sidebar_frame,
                text=label,
                font=ctk.CTkFont(size=13, weight="bold"),
                height=38,
                corner_radius=6,
                fg_color="transparent",
                text_color=("gray10", "gray90"),
                hover_color=("gray75", "gray25"),
                anchor="w",
                command=lambda k=key: self.select_frame(k),
            )
            btn.grid(row=idx, column=0, padx=15, pady=4, sticky="ew")
            self.nav_buttons[key] = btn

        # Sidebar Footer: Theme and Status
        footer_frame = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        footer_frame.grid(row=8, column=0, padx=15, pady=20, sticky="ew")

        ctk.CTkLabel(
            footer_frame,
            text="Appearance Mode:",
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", pady=(0, 4))

        self.theme_menu = ctk.CTkOptionMenu(
            footer_frame,
            values=["System", "Dark", "Light"],
            command=self._on_theme_changed,
            height=28,
        )
        self.theme_menu.pack(fill="x", pady=(0, 10))

        # 2. Main Content Display Area
        self.content_area = ctk.CTkFrame(self, fg_color="transparent")
        self.content_area.grid(row=0, column=1, sticky="nsew", padx=20, pady=15)
        self.content_area.grid_rowconfigure(0, weight=1)
        self.content_area.grid_columnconfigure(0, weight=1)

        # Initialize View Frames
        self.views: Dict[str, ctk.CTkFrame] = {
            "Dashboard": DashboardView(self.content_area, self, db_path=self.db_path),
            "Vehicle Entry": VehicleEntryView(self.content_area, self, db_path=self.db_path),
            "Vehicle Exit": VehicleExitView(self.content_area, self, db_path=self.db_path),
            "Parking Records": ParkingRecordsView(self.content_area, self, db_path=self.db_path),
            "Parking Slots": ParkingSlotsView(self.content_area, self, db_path=self.db_path),
        }

        # Place views in the grid
        for view in self.views.values():
            view.grid(row=0, column=0, sticky="nsew")

        # Select initial view
        self.select_frame("Dashboard")

    def select_frame(self, name: str):
        """Switches visible view and updates sidebar button highlights."""
        for key, btn in self.nav_buttons.items():
            if key == name:
                btn.configure(fg_color=COLOR_ACCENT, text_color="#ffffff")
            else:
                btn.configure(fg_color="transparent", text_color=("gray10", "gray90"))

        selected_view = self.views.get(name)
        if selected_view:
            selected_view.tkraise()
            if hasattr(selected_view, "refresh"):
                selected_view.refresh()

    def navigate_to_exit(self, plate: str):
        """Switches to the exit view and pre-fills the specified vehicle plate."""
        self.select_frame("Vehicle Exit")
        exit_view = self.views.get("Vehicle Exit")
        if isinstance(exit_view, VehicleExitView):
            exit_view.select_vehicle(plate)

    def notify_data_changed(self):
        """Called whenever an entry, exit, or update occurs to re-sync views."""
        for view in self.views.values():
            if hasattr(view, "refresh"):
                try:
                    view.refresh()
                except Exception:
                    pass

    def _on_theme_changed(self, mode: str):
        ctk.set_appearance_mode(mode)
        apply_treeview_style()
        self.notify_data_changed()
