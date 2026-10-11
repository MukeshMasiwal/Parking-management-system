import customtkinter as ctk
from database import init_db

class ParkingApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        # --- Window Configuration ---
        self.title("Parking Management System")
        self.geometry("900x600")

        # Configure grid layout (1 row, 2 columns)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        # --- Navigation Frame (Sidebar) ---
        self.navigation_frame = ctk.CTkFrame(self, corner_radius=0)
        self.navigation_frame.grid(row=0, column=0, sticky="nsew")
        self.navigation_frame.grid_rowconfigure(6, weight=1) # Pushes content up

        # Sidebar Title
        self.navigation_label = ctk.CTkLabel(
            self.navigation_frame, 
            text="Parking System", 
            font=ctk.CTkFont(size=20, weight="bold")
        )
        self.navigation_label.grid(row=0, column=0, padx=20, pady=20)

        # Navigation Buttons
        self.nav_dashboard = ctk.CTkButton(
            self.navigation_frame, 
            text="Dashboard", 
            command=lambda: self.select_frame("Dashboard")
        )
        self.nav_dashboard.grid(row=1, column=0, padx=20, pady=10)

        self.nav_defects = ctk.CTkButton(
            self.navigation_frame, 
            text="Defect Logging", 
            command=lambda: self.select_frame("Defect Logging")
        )
        self.nav_defects.grid(row=2, column=0, padx=20, pady=10)

        self.nav_charts = ctk.CTkButton(
            self.navigation_frame, 
            text="SQC Charts", 
            command=lambda: self.select_frame("SQC Charts")
        )
        self.nav_charts.grid(row=3, column=0, padx=20, pady=10)

        self.nav_help = ctk.CTkButton(
            self.navigation_frame, 
            text="Help / FAQ", 
            command=lambda: self.select_frame("Help / FAQ")
        )
        self.nav_help.grid(row=4, column=0, padx=20, pady=10)

        self.nav_about = ctk.CTkButton(
            self.navigation_frame, 
            text="About", 
            command=lambda: self.select_frame("About")
        )
        self.nav_about.grid(row=5, column=0, padx=20, pady=10)

        # --- Main Content Frame ---
        self.main_frame = ctk.CTkFrame(self)
        self.main_frame.grid(row=0, column=1, sticky="nsew", padx=20, pady=20)
        self.main_frame.grid_rowconfigure(0, weight=1)
        self.main_frame.grid_columnconfigure(0, weight=1)
        
        # Placeholder Label for currently selected section
        self.current_section_label = ctk.CTkLabel(
            self.main_frame, 
            text="Welcome to the Parking Management System\nSelect an option from the menu.", 
            font=ctk.CTkFont(size=24, weight="bold")
        )
        self.current_section_label.grid(row=0, column=0)

    def select_frame(self, name):
        """Updates the main frame to show the selected section."""
        self.current_section_label.configure(text=f"{name} Section\n(Implementation Pending)")

def main():
    # Initialize the SQLite database automatically on launch
    init_db()

    # Configure appearance
    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")
    
    # Initialize and run app
    app = ParkingApp()
    app.mainloop()

if __name__ == "__main__":
    main()
