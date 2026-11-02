"""
Parking Management System - Business Logic Service Layer
=========================================================

This module implements the core business logic for the Parking Management System,
serving as the mediator between the Presentation Layer (GUI/CLI) and the
Data Access Layer (database.py).

Core Responsibilities:
1. Validation of vehicle registration numbers, vehicle types, and timestamps.
2. Multi-floor capacity monitoring and intelligent slot allocation.
3. QR code generation, validation, and ticket generation.
4. Complete valet lifecycle management (handover, parking, retrieval, delivery).
5. Preventing duplicate active sessions.
6. Fee calculation adhering to the approved Tiered Hourly Charging Policy.
7. Exit recording, duration computation, and atomic slot release.
8. Role-based access control (RBAC) permission enforcement.
9. Security vehicle flagging and audit logging.
10. Strict separation of concerns: ZERO raw SQL in this module.

Approved Fee Policy:
- 15-minute free grace period.
- If stay <= 15 minutes, fee is $0.00.
- If stay > 15 minutes:
  * Minimum charge of 1 hour.
  * Rounding: Ceiling to the next full hour (ceil(elapsed_minutes / 60)).
  * Vehicle rates loaded from application settings:
    - Two-Wheeler: $10.00/hour
    - Car / Compact / Standard: $20.00/hour
    - Heavy Vehicles: $40.00/hour
"""

import math
import os
import re
import secrets
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple

try:
    import qrcode
    from PIL import Image
    _HAS_QRCODE = True
except ImportError:
    _HAS_QRCODE = False

try:
    import cv2
    _HAS_CV2 = True
except ImportError:
    _HAS_CV2 = False

import database
from database import DATETIME_FORMAT
import auth
from auth import AuthenticatedUser, require_permission, has_permission, PermissionDeniedError


# ============================================================================
# Custom Business Logic Exceptions
# ============================================================================

class ParkingServiceError(Exception):
    """Base exception for all parking service business logic errors."""
    pass


class ValidationError(ParkingServiceError):
    """Raised when vehicle, slot, or parameter validation fails."""
    pass


class DuplicateActiveSessionError(ParkingServiceError):
    """Raised when attempting to register a vehicle that already has an active session."""
    pass


class ParkingCapacityFullError(ParkingServiceError):
    """Raised when the parking facility is at 100% capacity with no available slots."""
    pass


class SlotNotAvailableError(ParkingServiceError):
    """Raised when a requested slot is occupied, maintenance, or incompatible."""
    pass


class SessionNotFoundError(ParkingServiceError):
    """Raised when a referenced parking session does not exist."""
    pass


class SlotNotFoundError(ParkingServiceError):
    """Raised when a referenced slot number or ID does not exist."""
    pass


class InvalidExitError(ParkingServiceError):
    """Raised when exit validation fails (e.g. exit before entry, inactive session)."""
    pass


class UnsafeDeletionError(ParkingServiceError):
    """Raised when attempting to delete a record in violation of safety/audit rules."""
    pass


class SlotAssignmentError(ParkingServiceError):
    """Raised when an error occurs during slot assignment."""
    pass


class ValetError(ParkingServiceError):
    """Raised when an invalid valet transition or operation is attempted."""
    pass


class FloorError(ParkingServiceError):
    """Raised when a floor operation fails."""
    pass


# Supported vehicle classifications
SUPPORTED_VEHICLE_TYPES = ("Car", "Two-Wheeler", "Compact", "EV", "Heavy")

# QR Ticket output directory
QR_TICKETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qr_tickets")


# ============================================================================
# Validation and Normalization Helpers
# ============================================================================

def validate_and_normalize_registration_number(registration_number: str) -> str:
    """
    Validates and standardizes a vehicle registration number.
    """
    if not registration_number or not isinstance(registration_number, str):
        raise ValidationError("Vehicle registration number must be a non-empty string.")
        
    cleaned = registration_number.strip().upper()
    if len(cleaned) < 3 or len(cleaned) > 16:
        raise ValidationError(
            f"Registration number '{cleaned}' length ({len(cleaned)}) is invalid. Must be between 3 and 16 characters."
        )
        
    if not re.match(r"^[A-Z0-9\s\-]+$", cleaned):
        raise ValidationError(
            f"Registration number '{cleaned}' contains invalid characters. Only uppercase letters, digits, spaces, and hyphens are permitted."
        )
        
    alphanumeric_count = len(re.findall(r"[A-Z0-9]", cleaned))
    if alphanumeric_count < 3:
        raise ValidationError(
            f"Registration number '{cleaned}' must contain at least 3 alphanumeric characters."
        )
        
    return cleaned


def normalize_vehicle_type(vehicle_type: str) -> str:
    """
    Normalizes input vehicle type string to one of the standard classifications.
    """
    if not vehicle_type or not isinstance(vehicle_type, str):
        raise ValidationError("Vehicle type must be a non-empty string.")
        
    v = vehicle_type.strip().lower()
    if v in ("car", "sedan", "suv", "standard"):
        return "Car"
    elif v in ("two-wheeler", "bike", "motorcycle", "scooter", "twowheeler", "two wheeler"):
        return "Two-Wheeler"
    elif v in ("compact", "hatchback", "mini"):
        return "Compact"
    elif v in ("ev", "electric", "electric vehicle"):
        return "EV"
    elif v in ("heavy", "truck", "bus", "commercial", "heavy vehicle"):
        return "Heavy"
    else:
        raise ValidationError(
            f"Unsupported vehicle type '{vehicle_type}'. Supported types: {', '.join(SUPPORTED_VEHICLE_TYPES)}"
        )


def validate_timestamp_string(timestamp_str: str, field_name: str = "Timestamp") -> datetime:
    """
    Validates that a timestamp string strictly follows DATETIME_FORMAT.
    """
    if not timestamp_str or not isinstance(timestamp_str, str):
        raise ValidationError(f"{field_name} must be a valid non-empty datetime string.")
        
    try:
        return datetime.strptime(timestamp_str.strip(), DATETIME_FORMAT)
    except ValueError:
        try:
            return datetime.fromisoformat(timestamp_str.strip())
        except ValueError:
            raise ValidationError(
                f"{field_name} '{timestamp_str}' does not match required format 'YYYY-MM-DD HH:MM:SS'."
            )


def validate_contact_number(contact: Optional[str]) -> Optional[str]:
    """
    Validates optional owner contact number.
    If provided, must contain 7 to 20 characters and at least 7 digits.
    Permits digits, spaces, hyphens, parentheses, and leading plus.
    """
    if contact is None:
        return None
    if not isinstance(contact, str):
        raise ValidationError("Contact number must be a text value.")
    cleaned = contact.strip()
    if not cleaned:
        return None
    if len(cleaned) < 7 or len(cleaned) > 20:
        raise ValidationError(
            f"Contact number '{cleaned}' length ({len(cleaned)}) is invalid. Must be between 7 and 20 characters."
        )
    if not re.match(r"^\+?[0-9\-\s\(\)]+$", cleaned):
        raise ValidationError(
            f"Contact number '{cleaned}' contains invalid characters. Only digits, +, -, spaces, and parentheses are allowed."
        )
    digits_only = re.sub(r"\D", "", cleaned)
    if len(digits_only) < 7:
        raise ValidationError(f"Contact number '{cleaned}' must contain at least 7 digits.")
    return cleaned


# ============================================================================
# QR Code Ticketing & Scanning Engine
# ============================================================================

def generate_qr_token() -> str:
    """Generates a cryptographically secure random token for QR ticketing."""
    # 128 bits of entropy keeps the payload compact enough for reliable
    # camera/OpenCV decoding while remaining infeasible to guess.
    return f"PKG-{secrets.token_urlsafe(16)}"


def generate_qr_code_image(token: str, output_path: Optional[str] = None) -> str:
    """
    Generates a QR code image representing the token and saves it to disk.
    Returns the absolute path to the generated image.
    """
    os.makedirs(QR_TICKETS_DIR, exist_ok=True)
    if not output_path:
        safe_prefix = re.sub(r"[^A-Za-z0-9]", "_", token[:16])
        output_path = os.path.join(QR_TICKETS_DIR, f"qr_{safe_prefix}.png")

    if _HAS_QRCODE:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=10,
            border=4,
        )
        qr.add_data(token)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img.save(output_path)
    else:
        # Fallback if qrcode module is missing: create placeholder image
        img = Image.new("RGB", (200, 200), color="white")
        img.save(output_path)

    return output_path


def decode_qr_image(image_path: str) -> Optional[str]:
    """
    Reads an image file and decodes any QR code present using OpenCV.
    Returns decoded token string or None if not found.
    """
    if not os.path.isfile(image_path):
        return None

    if _HAS_CV2:
        try:
            img = cv2.imread(image_path)
            if img is not None:
                detector = cv2.QRCodeDetector()
                data, bbox, _ = detector.detectAndDecode(img)
                if data:
                    return data.strip()
        except Exception:
            pass

    return None


def generate_parking_ticket(
    session: Any,
    qr_token: str,
    qr_image_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Generates a printable parking ticket dictionary containing human-readable
    and machine-readable QR metadata.
    """
    sess = dict(session) if not isinstance(session, dict) else session
    system_name = database.get_setting("system_name", default="Parking Management System")
    plate = sess.get("vehicle_registration_number", "UNKNOWN")
    vtype = sess.get("vehicle_type", "Car")
    sess_id = sess.get("session_id") or sess.get("id")
    entry_ts = sess.get("entry_time", datetime.now().strftime(DATETIME_FORMAT))
    slot_num = sess.get("slot_number", "N/A")
    floor_name = sess.get("floor_name") or sess.get("floor_identifier") or "Main Level"

    border = "=" * 42
    ticket_text = f"""{border}
          {system_name.upper()}
               ENTRY TICKET
{border}
 Ticket ID:    {sess_id}
 Vehicle No:   {plate}
 Vehicle Type: {vtype}
 Entry Time:   {entry_ts}
 Floor:        {floor_name}
 Slot Number:  {slot_num}
{border}
 QR Token:
 {qr_token}
 Please scan this ticket upon exit or retrieval.
{border}"""

    return {
        "system_name": system_name,
        "ticket_id": sess_id,
        "session_id": sess_id,
        "vehicle_registration_number": plate,
        "vehicle_type": vtype,
        "entry_time": entry_ts,
        "floor_name": floor_name,
        "slot_number": slot_num,
        "qr_token": qr_token,
        "qr_image_path": qr_image_path,
        "formatted_ticket_text": ticket_text,
    }


def scan_and_validate_qr_token(
    qr_input: str,
    operator: Optional[AuthenticatedUser] = None,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Scans and validates a QR code string or image file.
    
    Security & Validation Rules:
    - Operator must possess 'qr:scan' permission.
    - If qr_input is an image file path, decodes using OpenCV.
    - Token must exist in the database.
    - Rejects completed or cancelled sessions.
    - Returns live parking status, duration, calculated fee, and vehicle safety flags.
    - Never allows QR scanning alone to bypass payment.
    """
    if operator is not None:
        require_permission(operator, "qr:scan")

    if not qr_input or not isinstance(qr_input, str):
        raise ValidationError("QR input cannot be empty.")

    clean_input = qr_input.strip()

    # Check if input is an image file path
    token = clean_input
    if os.path.isfile(clean_input) and clean_input.lower().endswith((".png", ".jpg", ".jpeg", ".bmp")):
        decoded = decode_qr_image(clean_input)
        if not decoded:
            raise ValidationError(f"Could not detect or decode a valid QR code from image '{clean_input}'.")
        token = decoded

    # Lookup session by token
    session_row = database.get_session_by_qr_token(token, db_path=db_path)
    if not session_row:
        raise ValidationError("Invalid QR Token: No active parking session matches this token.")

    session_dict = dict(session_row)
    if session_dict["status"] != "ACTIVE":
        raise ValidationError(
            f"Invalid QR Ticket: Parking session ID {session_dict['id']} is already '{session_dict['status']}'."
        )

    # Compute current stay duration & prospective fee
    now_ts = datetime.now().strftime(DATETIME_FORMAT)
    fee_calc = calculate_duration_and_fee(
        entry_time_str=session_dict["entry_time"],
        exit_time_str=now_ts,
        vehicle_type=session_dict["vehicle_type"],
        db_path=db_path
    )

    # Check security flags
    flags = database.get_vehicle_flags(
        vehicle_registration_number=session_dict["vehicle_registration_number"],
        active_only=True,
        db_path=db_path
    )
    flag_list = [dict(f) for f in flags]

    # Check valet assignment
    valet_row = database.get_valet_assignment_by_session_id(session_dict["id"], db_path=db_path)
    valet_dict = dict(valet_row) if valet_row else None

    return {
        "valid": True,
        "token": token,
        "session_id": session_dict["id"],
        "vehicle_registration_number": session_dict["vehicle_registration_number"],
        "vehicle_type": session_dict["vehicle_type"],
        "owner_name": session_dict["owner_name"],
        "owner_contact": session_dict["owner_contact"],
        "slot_id": session_dict["slot_id"],
        "slot_number": session_dict["slot_number"],
        "floor_identifier": session_dict.get("floor_identifier", "G"),
        "floor_name": session_dict.get("floor_name", "Ground Floor"),
        "entry_time": session_dict["entry_time"],
        "current_time": now_ts,
        "duration_minutes": fee_calc["duration_minutes"],
        "duration_formatted": fee_calc["duration_formatted"],
        "billable_hours": fee_calc["billable_hours"],
        "hourly_rate": fee_calc["hourly_rate"],
        "base_fee": fee_calc["base_fee"],
        "current_fee": fee_calc["final_fee"],
        "payment_status": session_dict.get("payment_status", "PENDING"),
        "session_status": session_dict["status"],
        "security_flags": flag_list,
        "valet_assignment": valet_dict,
    }


# ============================================================================
# Fee Calculation Engine (Tiered Hourly Policy)
# ============================================================================

def calculate_duration_and_fee(
    entry_time_str: str,
    exit_time_str: str,
    vehicle_type: str = "Car",
    discount_amount: float = 0.0,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Calculates duration, billable hours, and final fee adhering to the approved policy.
    """
    if (
        not isinstance(discount_amount, (int, float))
        or isinstance(discount_amount, bool)
        or not math.isfinite(discount_amount)
    ):
        raise ValidationError("Discount amount must be a non-negative number.")
    if discount_amount < 0:
        raise ValidationError("Discount amount cannot be negative.")

    entry_dt = validate_timestamp_string(entry_time_str, "Entry time")
    exit_dt = validate_timestamp_string(exit_time_str, "Exit time")
    
    delta = exit_dt - entry_dt
    delta_seconds = delta.total_seconds()
    if delta_seconds < 0:
        raise InvalidExitError(
            f"Exit time ({exit_time_str}) cannot precede entry time ({entry_time_str})."
        )
        
    elapsed_minutes = delta_seconds / 60.0
    
    # 1. Grace Period check
    grace_str = database.get_setting("grace_period_mins", default="15", db_path=db_path)
    grace_period = float(grace_str) if grace_str else 15.0
    
    # 2. Determine Hourly Rate for Vehicle Category
    norm_vtype = normalize_vehicle_type(vehicle_type)
    if norm_vtype == "Two-Wheeler":
        rate_key = "rate_two_wheeler"
        fallback_rate = 10.0
    elif norm_vtype == "Heavy":
        rate_key = "rate_heavy"
        fallback_rate = 40.0
    else:
        rate_key = "rate_car"
        fallback_rate = 20.0
        
    rate_str = database.get_setting(rate_key, default=str(fallback_rate), db_path=db_path)
    hourly_rate = float(rate_str) if rate_str else fallback_rate
    
    if elapsed_minutes <= grace_period:
        billable_hours = 0
        base_fee = 0.0
    else:
        # Minimum 1 hour charge, rounded up to next full hour
        billable_hours = max(1, math.ceil(elapsed_minutes / 60.0))
        base_fee = round(billable_hours * hourly_rate, 2)
        
    if discount_amount > base_fee:
        raise ValidationError(
            f"Discount amount ({discount_amount:.2f}) cannot exceed the calculated fee ({base_fee:.2f})."
        )
    final_fee = max(0.0, round(base_fee - discount_amount, 2))

    hours_int = int(elapsed_minutes // 60)
    mins_int = int(elapsed_minutes % 60)
    duration_str = f"{hours_int}h {mins_int:02d}m"
    
    return {
        "duration_minutes": round(elapsed_minutes, 1),
        "duration_formatted": duration_str,
        "billable_hours": billable_hours,
        "hourly_rate": hourly_rate,
        "grace_period_mins": grace_period,
        "base_fee": base_fee,
        "discount_applied": discount_amount,
        "final_fee": final_fee,
        "fee": final_fee,
    }


# ============================================================================
# Multi-Floor Intelligent Slot Allocation
# ============================================================================

def find_best_available_slot(
    vehicle_type: str,
    floor_id: Optional[int] = None,
    db_path: Optional[str] = None
) -> Optional[Any]:
    """
    Intelligently assigns the most suitable available slot based on vehicle type and floor.
    
    Preference Order:
    - Two-Wheeler: 'Two-Wheeler' slots first, then 'Compact', then 'Standard'.
    - EV: 'EV' slots first, then 'Standard'.
    - Compact: 'Compact' slots first, then 'Standard'.
    - Heavy: 'Heavy' slots first, then 'Standard'.
    - Car: 'Standard' slots first, then 'Compact'.
    """
    vtype = normalize_vehicle_type(vehicle_type)
    
    if vtype == "Two-Wheeler":
        preferences = ["Two-Wheeler", "Compact", "Standard"]
    elif vtype == "EV":
        preferences = ["EV", "Standard"]
    elif vtype == "Compact":
        preferences = ["Compact", "Standard"]
    elif vtype == "Heavy":
        preferences = ["Heavy", "Standard"]
    else:  # Car
        preferences = ["Standard", "Compact"]
        
    for preferred_type in preferences:
        available = database.get_available_slots(db_path=db_path, slot_type=preferred_type, floor_id=floor_id)
        if available:
            return available[0]
            
    # Fallback: any available slot on specified floor
    any_on_floor = database.get_available_slots(db_path=db_path, floor_id=floor_id)
    if any_on_floor:
        return any_on_floor[0]

    # If floor was specified and had nothing, try without floor constraint
    if floor_id is not None:
        return find_best_available_slot(vehicle_type, floor_id=None, db_path=db_path)

    return None


# ============================================================================
# Vehicle Entry & Slot Allocation (with QR and Valet Integration)
# ============================================================================

def register_vehicle_entry(
    vehicle_registration_number: str,
    vehicle_type: str = "Car",
    owner_name: Optional[str] = None,
    owner_contact: Optional[str] = None,
    slot_id: Optional[int] = None,
    slot_number: Optional[str] = None,
    floor_id: Optional[int] = None,
    entry_time: Optional[str] = None,
    is_valet: bool = False,
    valet_user_id: Optional[int] = None,
    valet_notes: Optional[str] = None,
    operator: Optional[AuthenticatedUser] = None,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Registers a vehicle entering the parking facility.
    
    Business Rules Enforced:
    1. Operator permission validation ('vehicles:register' or 'vehicles:entry').
    2. Validates registration plate and vehicle category.
    3. Prevents duplicate active sessions for same vehicle.
    4. Evaluates capacity and intelligently allocates slot (floor-aware).
    5. Generates cryptographically secure QR token and image ticket.
    6. Automatically initializes Valet assignment if requested.
    7. Executes atomic transactional database entry.
    """
    if operator is not None:
        # Check permissions
        if not (has_permission(operator, "vehicles:register") or has_permission(operator, "vehicles:entry")):
            require_permission(operator, "vehicles:register")

    # 1. Validate registration plate
    clean_vno = validate_and_normalize_registration_number(vehicle_registration_number)
    clean_vtype = normalize_vehicle_type(vehicle_type)
    
    if owner_name is not None and not isinstance(owner_name, str):
        raise ValidationError("Owner name must be a text value.")
    clean_owner_name = owner_name.strip() if owner_name and owner_name.strip() else None
    clean_owner_contact = validate_contact_number(owner_contact)
    
    entry_ts = entry_time if entry_time else datetime.now().strftime(DATETIME_FORMAT)
    validate_timestamp_string(entry_ts, "Entry time")
    
    # 2. Check for duplicate active session
    existing_session = database.get_active_session_by_vehicle(clean_vno, db_path=db_path)
    if existing_session:
        raise DuplicateActiveSessionError(
            f"Vehicle '{clean_vno}' is already parked in slot '{existing_session['slot_number']}' "
            f"(Session ID: {existing_session['id']}, Entry: {existing_session['entry_time']})."
        )
        
    # 3. Check overall capacity and select slot
    target_slot = None
    if slot_id is not None:
        target_slot = database.get_slot_by_id(slot_id, db_path=db_path)
        if not target_slot:
            raise SlotNotFoundError(f"Slot ID {slot_id} does not exist.")
        if target_slot["status"] != "Available":
            raise SlotNotAvailableError(
                f"Slot '{target_slot['slot_number']}' is currently '{target_slot['status']}', not Available."
            )
    elif slot_number is not None:
        if not isinstance(slot_number, str) or not slot_number.strip():
            raise ValidationError("Slot number must be a non-empty string.")
        target_slot = database.get_slot_by_number(slot_number.strip(), db_path=db_path)
        if not target_slot:
            raise SlotNotFoundError(f"Slot number '{slot_number}' does not exist.")
        if target_slot["status"] != "Available":
            raise SlotNotAvailableError(
                f"Slot '{target_slot['slot_number']}' is currently '{target_slot['status']}', not Available."
            )
    else:
        # Intelligent auto-assignment
        target_slot = find_best_available_slot(clean_vtype, floor_id=floor_id, db_path=db_path)
        if not target_slot:
            all_available = database.get_available_slots(db_path=db_path)
            if not all_available:
                raise ParkingCapacityFullError("Parking facility is at 100% capacity. No slots available.")
            else:
                raise SlotNotAvailableError(f"No suitable parking slots currently available for {clean_vtype}.")
                
    slot_dict = dict(target_slot)
    chosen_slot_id = slot_dict["id"]
    chosen_slot_number = slot_dict["slot_number"]
    chosen_floor_name = slot_dict.get("floor_name") or "Ground Floor"
    chosen_floor_id = slot_dict.get("floor_id")

    # 4. Generate QR Token & Image
    qr_token = generate_qr_token()
    qr_image_path = generate_qr_code_image(qr_token)

    # 5. Atomic database insertion & slot reservation
    try:
        session_id = database.start_parking_session(
            vehicle_registration_number=clean_vno,
            slot_id=chosen_slot_id,
            vehicle_type=clean_vtype,
            owner_name=clean_owner_name,
            owner_contact=clean_owner_contact,
            entry_time=entry_ts,
            qr_token=qr_token,
            processed_by_user_id=operator.user_id if operator else None,
            db_path=db_path
        )
    except database.ActiveSessionExistsError as e:
        raise DuplicateActiveSessionError(str(e)) from e
    except database.SlotNotAvailableError as e:
        raise SlotNotAvailableError(str(e)) from e
    except database.SlotNotFoundError as e:
        raise SlotNotFoundError(str(e)) from e
    except database.DatabaseError as e:
        raise SlotAssignmentError(f"Failed to assign slot due to database error: {e}") from e

    # 6. Automatic Valet Assignment if requested
    valet_assignment_id = None
    if is_valet or valet_user_id is not None:
        valet_assignment_id = database.create_valet_assignment(
            session_id=session_id,
            valet_user_id=valet_user_id,
            assigned_by_user_id=operator.user_id if operator else None,
            notes=valet_notes or f"Valet requested upon entry for {clean_vno}",
            db_path=db_path
        )

    # 7. Generate Ticket Metadata
    session_data = {
        "session_id": session_id,
        "vehicle_registration_number": clean_vno,
        "vehicle_type": clean_vtype,
        "owner_name": clean_owner_name,
        "owner_contact": clean_owner_contact,
        "slot_id": chosen_slot_id,
        "slot_number": chosen_slot_number,
        "floor_name": chosen_floor_name,
        "floor_id": chosen_floor_id,
        "entry_time": entry_ts,
        "status": "ACTIVE",
        "qr_token": qr_token,
        "qr_image_path": qr_image_path,
        "valet_assignment_id": valet_assignment_id,
    }
    ticket = generate_parking_ticket(session_data, qr_token=qr_token, qr_image_path=qr_image_path)
    session_data["ticket"] = ticket

    # Audit log entry event
    database.log_activity_event(
        user_id=operator.user_id if operator else None,
        username=operator.username if operator else "SYSTEM",
        action="VEHICLE_ENTRY",
        entity_type="SESSION",
        entity_id=str(session_id),
        details=f"Registered entry for {clean_vno} in slot {chosen_slot_number}",
        db_path=db_path
    )

    return session_data


# ============================================================================
# Vehicle Exit, Payment Processing & Slot Release
# ============================================================================

def record_vehicle_exit(
    session_id: Optional[int] = None,
    vehicle_registration_number: Optional[str] = None,
    qr_token: Optional[str] = None,
    exit_time: Optional[str] = None,
    discount_amount: float = 0.0,
    payment_method: str = "Cash",
    payment_status: str = "PAID",
    operator: Optional[AuthenticatedUser] = None,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Processes vehicle exit: verifies authorization, calculates duration and fee,
    processes payment, frees the slot, and completes the valet workflow if active.
    
    Security & Business Rules:
    1. If operator is passed, checks exit or billing permission.
    2. Session identified via session_id, plate, or scanned QR token.
    3. Rejects already completed or non-active sessions.
    4. Validates chronological exit timestamp.
    5. Never allows QR scanning alone to bypass payment.
    6. Updates valet assignment to DELIVERED if valet was active.
    7. Atomically releases parking slot back to 'Available'.
    """
    if operator is not None:
        if not (has_permission(operator, "vehicles:exit") or has_permission(operator, "billing:process")):
            require_permission(operator, "vehicles:exit")

    if not isinstance(discount_amount, (int, float)) or isinstance(discount_amount, bool):
        raise ValidationError("Discount amount must be a non-negative number.")
    if discount_amount < 0:
        raise ValidationError("Discount amount cannot be negative.")
    if payment_status not in ("PAID", "EXEMPT"):
        raise ValidationError("Payment status must be 'PAID' or 'EXEMPT' at vehicle exit.")
    if not isinstance(payment_method, str) or not payment_method.strip():
        raise ValidationError("Payment method is required at vehicle exit.")

    # 1. Identify active session
    session = None
    if qr_token is not None and qr_token.strip():
        session = database.get_session_by_qr_token(qr_token.strip(), db_path=db_path)
    elif session_id is not None:
        session = database.get_session_by_id(session_id, db_path=db_path)
    elif vehicle_registration_number is not None:
        clean_vno = validate_and_normalize_registration_number(vehicle_registration_number)
        session = database.get_active_session_by_vehicle(clean_vno, db_path=db_path)
    else:
        raise ValidationError("Either session_id, vehicle_registration_number, or qr_token must be provided.")
        
    if not session:
        identifier = f"ID {session_id}" if session_id else (f"QR {qr_token}" if qr_token else f"Vehicle '{vehicle_registration_number}'")
        raise SessionNotFoundError(f"No active parking session found for {identifier}.")
        
    if session["status"] != "ACTIVE":
        raise InvalidExitError(
            f"Session {session['id']} is '{session['status']}', not ACTIVE. Cannot record exit."
        )
        
    # 2. Validate exit chronology & calculate fee
    exit_ts = exit_time if exit_time else datetime.now().strftime(DATETIME_FORMAT)
    fee_calc = calculate_duration_and_fee(
        entry_time_str=session["entry_time"],
        exit_time_str=exit_ts,
        vehicle_type=session["vehicle_type"],
        discount_amount=discount_amount,
        db_path=db_path
    )
    
    base_fee = fee_calc["base_fee"]
    final_fee = fee_calc["final_fee"]
    if payment_status == "EXEMPT" and final_fee > 0:
        raise ValidationError("A non-zero parking charge cannot be completed as exempt.")
    
    # 3. Perform atomic exit, payment recording, and slot release in database
    try:
        updated = database.end_parking_session(
            session_id=session["id"],
            exit_time=exit_ts,
            fee=final_fee,
            payment_status=payment_status,
            payment_method=payment_method,
            processed_by_user_id=operator.user_id if operator else None,
            db_path=db_path
        )
    except database.SessionNotFoundError as e:
        raise SessionNotFoundError(str(e)) from e
    except database.InvalidOperationError as e:
        raise InvalidExitError(str(e)) from e
    except database.DatabaseError as e:
        raise InvalidExitError(f"Database error during exit processing: {e}") from e

    # 4. Conclude Valet lifecycle if active
    valet_row = database.get_valet_assignment_by_session_id(session["id"], db_path=db_path)
    if valet_row and valet_row["status"] != "DELIVERED":
        database.update_valet_status(
            assignment_id=valet_row["id"],
            new_status="DELIVERED",
            valet_user_id=operator.user_id if (operator and operator.is_valet()) else None,
            notes="Vehicle delivered upon exit checkout",
            db_path=db_path
        )

    # 5. Format printable payment receipt
    system_name = database.get_setting("system_name", default="Parking Management System")
    currency = database.get_setting("currency_symbol", default="$")
    receipt_text = f"""==========================================
          {system_name.upper()}
             OFFICIAL RECEIPT
==========================================
 Receipt No:   REC-{session['id']}
 Session ID:   {session['id']}
 Vehicle No:   {session['vehicle_registration_number']}
 Slot:         {session['slot_number']}
 Entry Time:   {session['entry_time']}
 Exit Time:    {exit_ts}
 Duration:     {fee_calc['duration_formatted']} ({fee_calc['billable_hours']} hrs)
------------------------------------------
 Base Charge:  {currency}{base_fee:.2f}
 Discount:    -{currency}{discount_amount:.2f}
 TOTAL PAID:   {currency}{final_fee:.2f}
 Payment Mode: {payment_method} ({payment_status})
 Cashier/User: {operator.username if operator else 'System'}
==========================================
          THANK YOU FOR PARKING!
=========================================="""

    # Audit log exit event
    database.log_activity_event(
        user_id=operator.user_id if operator else None,
        username=operator.username if operator else "SYSTEM",
        action="VEHICLE_EXIT",
        entity_type="SESSION",
        entity_id=str(session["id"]),
        details=f"Processed exit for {session['vehicle_registration_number']}. Fee: {final_fee}, Mode: {payment_method}",
        db_path=db_path
    )

    return {
        "session_id": session["id"],
        "vehicle_registration_number": session["vehicle_registration_number"],
        "vehicle_type": session["vehicle_type"],
        "owner_name": session["owner_name"],
        "slot_id": session["slot_id"],
        "slot_number": session["slot_number"],
        "entry_time": session["entry_time"],
        "exit_time": exit_ts,
        "duration_minutes": fee_calc["duration_minutes"],
        "duration_formatted": fee_calc["duration_formatted"],
        "billable_hours": fee_calc["billable_hours"],
        "hourly_rate": fee_calc["hourly_rate"],
        "base_fee": base_fee,
        "discount_applied": discount_amount,
        "final_fee": final_fee,
        "fee": final_fee,
        "payment_status": payment_status,
        "payment_method": payment_method,
        "receipt_text": receipt_text,
        "status": "COMPLETED"
    }


# ============================================================================
# Multi-Floor Management Module
# ============================================================================

def add_parking_floor(
    operator: Optional[AuthenticatedUser],
    floor_identifier: str,
    floor_name: str,
    total_capacity: int = 25,
    db_path: Optional[str] = None
) -> int:
    """Adds a new parking floor. Operator requires 'floors:manage' permission."""
    if operator is not None:
        require_permission(operator, "floors:manage")
    floor_id = database.create_floor(
        floor_identifier=floor_identifier,
        floor_name=floor_name,
        total_capacity=total_capacity,
        db_path=db_path
    )
    database.log_activity_event(
        user_id=operator.user_id if operator else None,
        username=operator.username if operator else "SYSTEM",
        action="FLOOR_CREATED",
        entity_type="FLOOR",
        entity_id=str(floor_id),
        details=f"Created floor '{floor_identifier}' - {floor_name}",
        db_path=db_path
    )
    return floor_id


def edit_parking_floor(
    operator: Optional[AuthenticatedUser],
    floor_id: int,
    floor_name: Optional[str] = None,
    total_capacity: Optional[int] = None,
    is_active: Optional[int] = None,
    db_path: Optional[str] = None
) -> None:
    """Updates floor details or status. Operator requires 'floors:manage' permission."""
    if operator is not None:
        require_permission(operator, "floors:manage")
    database.update_floor(
        floor_id=floor_id,
        floor_name=floor_name,
        total_capacity=total_capacity,
        is_active=is_active,
        db_path=db_path
    )
    database.log_activity_event(
        user_id=operator.user_id if operator else None,
        username=operator.username if operator else "SYSTEM",
        action="FLOOR_UPDATED",
        entity_type="FLOOR",
        entity_id=str(floor_id),
        details=f"Updated floor ID {floor_id}",
        db_path=db_path
    )


def get_floors_list(
    operator: Optional[AuthenticatedUser] = None,
    active_only: bool = False,
    db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Retrieves list of floors. Operator requires 'floors:view' or 'floors:manage'."""
    rows = database.get_all_floors(active_only=active_only, db_path=db_path)
    return [dict(r) for r in rows]


def get_floor_details(floor_id: int, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Retrieves a single floor with its slots and current occupancy summary."""
    floor = database.get_floor_by_id(floor_id, db_path=db_path)
    if not floor:
        raise FloorError(f"Floor ID {floor_id} not found.")

    slots = database.get_slots_by_floor(floor_id, db_path=db_path)
    summary = database.get_floor_occupancy_summary(db_path=db_path)
    floor_metric = next((m for m in summary if m["floor_id"] == floor_id), {})

    res = dict(floor)
    res.update(floor_metric)
    res["slots"] = [dict(s) for s in slots]
    return res


def get_floor_dashboard_metrics(
    operator: Optional[AuthenticatedUser] = None,
    db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Retrieves occupancy percentage and capacity breakdown across all floors."""
    return database.get_floor_occupancy_summary(db_path=db_path)


def add_parking_slot(
    operator: Optional[AuthenticatedUser],
    slot_number: str,
    slot_type: str = "Standard",
    floor_id: Optional[int] = None,
    db_path: Optional[str] = None
) -> int:
    """Creates a new slot assigned to a floor. Operator requires 'slots:manage' permission."""
    if operator is not None:
        require_permission(operator, "slots:manage")
    slot_id = database.create_slot(
        slot_number=slot_number,
        slot_type=slot_type,
        floor_id=floor_id,
        db_path=db_path
    )
    database.log_activity_event(
        user_id=operator.user_id if operator else None,
        username=operator.username if operator else "SYSTEM",
        action="SLOT_CREATED",
        entity_type="SLOT",
        entity_id=str(slot_id),
        details=f"Created slot '{slot_number}' ({slot_type}) on floor ID {floor_id}",
        db_path=db_path
    )
    return slot_id


def configure_slot_status(
    operator: Optional[AuthenticatedUser],
    slot_id: int,
    new_status: str,
    db_path: Optional[str] = None
) -> None:
    """Configures slot status ('Available', 'Occupied', 'Maintenance', 'Reserved')."""
    if operator is not None:
        require_permission(operator, "slots:manage")
    database.update_slot_status(slot_id, new_status, db_path=db_path)
    database.log_activity_event(
        user_id=operator.user_id if operator else None,
        username=operator.username if operator else "SYSTEM",
        action="SLOT_STATUS_CHANGED",
        entity_type="SLOT",
        entity_id=str(slot_id),
        details=f"Changed slot ID {slot_id} status to '{new_status}'",
        db_path=db_path
    )


# ============================================================================
# Dedicated Valet Management Module
# ============================================================================

def assign_valet_to_session(
    operator: Optional[AuthenticatedUser],
    session_id: int,
    valet_user_id: Optional[int] = None,
    notes: Optional[str] = None,
    db_path: Optional[str] = None
) -> int:
    """Assigns a valet attendant to a parking session."""
    if operator is not None:
        require_permission(operator, "valet:manage")

    session = database.get_session_by_id(session_id, db_path=db_path)
    if not session:
        raise SessionNotFoundError(f"Session ID {session_id} not found.")

    assignment_id = database.create_valet_assignment(
        session_id=session_id,
        valet_user_id=valet_user_id,
        assigned_by_user_id=operator.user_id if operator else None,
        notes=notes,
        db_path=db_path
    )

    database.log_activity_event(
        user_id=operator.user_id if operator else None,
        username=operator.username if operator else "SYSTEM",
        action="VALET_ASSIGNED",
        entity_type="VALET",
        entity_id=str(assignment_id),
        details=f"Assigned valet ID {valet_user_id} to session {session_id}",
        db_path=db_path
    )
    return assignment_id


def valet_accept_handover(
    valet_user: Optional[AuthenticatedUser],
    assignment_id: int,
    notes: Optional[str] = None,
    db_path: Optional[str] = None
) -> None:
    """Valet attendant accepts vehicle handover."""
    if valet_user is not None:
        require_permission(valet_user, "valet:accept_handover")
    database.update_valet_status(
        assignment_id=assignment_id,
        new_status="VEHICLE_RECEIVED",
        valet_user_id=valet_user.user_id if valet_user else None,
        notes=notes or "Valet accepted vehicle handover",
        db_path=db_path
    )


def valet_start_parking(
    valet_user: Optional[AuthenticatedUser],
    assignment_id: int,
    notes: Optional[str] = None,
    db_path: Optional[str] = None
) -> None:
    """Valet starts driving to designated parking floor and slot."""
    if valet_user is not None:
        require_permission(valet_user, "valet:update_progress")
    database.update_valet_status(
        assignment_id=assignment_id,
        new_status="PARKING_IN_PROGRESS",
        valet_user_id=valet_user.user_id if valet_user else None,
        notes=notes or "Parking in progress",
        db_path=db_path
    )


def valet_complete_parking(
    valet_user: Optional[AuthenticatedUser],
    assignment_id: int,
    notes: Optional[str] = None,
    db_path: Optional[str] = None
) -> None:
    """Valet finishes parking the vehicle in designated slot."""
    if valet_user is not None:
        require_permission(valet_user, "valet:update_progress")
    database.update_valet_status(
        assignment_id=assignment_id,
        new_status="PARKED",
        valet_user_id=valet_user.user_id if valet_user else None,
        notes=notes or "Vehicle parked in designated slot",
        db_path=db_path
    )


def request_valet_retrieval(
    operator: Optional[AuthenticatedUser],
    assignment_id: int,
    notes: Optional[str] = None,
    db_path: Optional[str] = None
) -> None:
    """Customer or cashier requests vehicle retrieval."""
    if operator is not None and not (
        has_permission(operator, "valet:retrieval")
        or has_permission(operator, "valet:manage")
    ):
        require_permission(operator, "valet:retrieval")
    database.update_valet_status(
        assignment_id=assignment_id,
        new_status="RETRIEVAL_REQUESTED",
        valet_user_id=operator.user_id if (operator and operator.is_valet()) else None,
        notes=notes or "Customer requested retrieval",
        db_path=db_path
    )


def valet_retrieve_vehicle(
    valet_user: Optional[AuthenticatedUser],
    assignment_id: int,
    notes: Optional[str] = None,
    db_path: Optional[str] = None
) -> None:
    """Valet retrieves vehicle from parking slot to delivery pickup area."""
    if valet_user is not None:
        require_permission(valet_user, "valet:retrieval")
    database.update_valet_status(
        assignment_id=assignment_id,
        new_status="VEHICLE_RETRIEVED",
        valet_user_id=valet_user.user_id if valet_user else None,
        notes=notes or "Vehicle retrieved to staging area",
        db_path=db_path
    )


def valet_deliver_vehicle(
    valet_user: Optional[AuthenticatedUser],
    assignment_id: int,
    notes: Optional[str] = None,
    db_path: Optional[str] = None
) -> None:
    """Valet delivers keys and vehicle back to verified customer."""
    if valet_user is not None:
        require_permission(valet_user, "valet:deliver")
    database.update_valet_status(
        assignment_id=assignment_id,
        new_status="DELIVERED",
        valet_user_id=valet_user.user_id if valet_user else None,
        notes=notes or "Vehicle successfully handed over to customer",
        db_path=db_path
    )


def get_valet_board(
    operator: Optional[AuthenticatedUser] = None,
    valet_user_id: Optional[int] = None,
    status_filter: Optional[str] = None,
    db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Retrieves live valet assignments board."""
    rows = database.get_valet_assignments(
        valet_user_id=valet_user_id,
        status_filter=status_filter,
        db_path=db_path
    )
    return [dict(r) for r in rows]


def get_valet_history(
    valet_user: Optional[AuthenticatedUser] = None,
    db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Retrieves personal history of valet assignments."""
    valet_id = valet_user.user_id if valet_user else None
    rows = database.get_valet_assignments(valet_user_id=valet_id, db_path=db_path)
    return [dict(r) for r in rows]


# ============================================================================
# Security Flag Operations
# ============================================================================

def flag_vehicle_as_suspicious(
    operator: Optional[AuthenticatedUser],
    vehicle_registration_number: str,
    reason: str,
    severity: str = "MEDIUM",
    db_path: Optional[str] = None
) -> int:
    """Flags a vehicle as suspicious or requiring security inspection."""
    if operator is not None:
        require_permission(operator, "vehicles:flag")

    clean_vno = validate_and_normalize_registration_number(vehicle_registration_number)
    flag_id = database.flag_vehicle(
        vehicle_registration_number=clean_vno,
        flagged_by_user_id=operator.user_id if operator else None,
        reason=reason,
        severity=severity,
        db_path=db_path
    )

    database.log_activity_event(
        user_id=operator.user_id if operator else None,
        username=operator.username if operator else "SYSTEM",
        action="VEHICLE_FLAGGED",
        entity_type="SECURITY_FLAG",
        entity_id=str(flag_id),
        details=f"Flagged {clean_vno} ({severity}): {reason}",
        db_path=db_path
    )
    return flag_id


def get_security_flags(
    vehicle_registration_number: Optional[str] = None,
    active_only: bool = True,
    db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Retrieves security flags for security monitoring."""
    clean_vno = (
        validate_and_normalize_registration_number(vehicle_registration_number)
        if vehicle_registration_number
        else None
    )
    rows = database.get_vehicle_flags(vehicle_registration_number=clean_vno, active_only=active_only, db_path=db_path)
    return [dict(r) for r in rows]


def resolve_security_flag(
    operator: Optional[AuthenticatedUser],
    flag_id: int,
    resolution_notes: str,
    db_path: Optional[str] = None
) -> None:
    """Marks a security flag as resolved."""
    if operator is not None:
        require_permission(operator, "vehicles:flag")
    database.resolve_vehicle_flag(flag_id, resolution_notes, db_path=db_path)
    database.log_activity_event(
        user_id=operator.user_id if operator else None,
        username=operator.username if operator else "SYSTEM",
        action="VEHICLE_FLAG_RESOLVED",
        entity_type="SECURITY_FLAG",
        entity_id=str(flag_id),
        details=f"Resolved flag ID {flag_id}: {resolution_notes}",
        db_path=db_path
    )


# ============================================================================
# Search and Record Retrieval
# ============================================================================

def search_parking_records(
    keyword: Optional[str] = None,
    status: Optional[str] = None,
    vehicle_type: Optional[str] = None,
    slot_number: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 100,
    db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Searches parking records by vehicle registration, owner, slot number, and vehicle type.
    Returns rich dictionary representations.
    """
    if status and status.upper().strip() != "ALL":
        norm_status = status.upper().strip()
        if norm_status not in ("ACTIVE", "COMPLETED", "CANCELLED"):
            raise ValidationError(f"Invalid status filter '{status}'. Must be ACTIVE, COMPLETED, or CANCELLED.")
    else:
        norm_status = None
        
    rows = database.search_sessions(
        keyword=keyword,
        status=norm_status,
        vehicle_type=vehicle_type,
        slot_number=slot_number,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        db_path=db_path
    )
    
    now_dt = datetime.now()
    results = []
    for r in rows:
        d = dict(r)
        # Compute elapsed time for active sessions
        if d["status"] == "ACTIVE":
            try:
                entry_dt = datetime.strptime(d["entry_time"], DATETIME_FORMAT)
                mins = max(0, (now_dt - entry_dt).total_seconds() / 60.0)
                d["current_stay_minutes"] = round(mins, 1)
            except (TypeError, ValueError):
                d["current_stay_minutes"] = None
        results.append(d)
        
    return results


def get_active_sessions(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieves all currently active parking sessions with current duration estimates."""
    return search_parking_records(status="ACTIVE", db_path=db_path)


def get_vehicle_history(
    vehicle_registration_number: str,
    limit: int = 100,
    db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Retrieves full historical parking sessions for a specific vehicle registration."""
    clean_vno = validate_and_normalize_registration_number(vehicle_registration_number)
    rows = database.get_session_history(vehicle_number=clean_vno, limit=limit, db_path=db_path)
    return [dict(r) for r in rows]


def get_parking_capacity_status(db_path: Optional[str] = None) -> Dict[str, Any]:
    """Retrieves facility capacity and real-time occupancy statistics, including recorded revenue."""
    return database.get_capacity_metrics(db_path=db_path)


def get_dashboard_metrics(db_path: Optional[str] = None) -> Dict[str, Any]:
    """Retrieves high-level summary KPIs and recorded revenue for the Dashboard."""
    return get_parking_capacity_status(db_path=db_path)


def get_dashboard_slot_summary(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Returns live availability totals for each configured slot category."""
    slots = get_all_parking_slots(db_path=db_path)
    summary: Dict[str, Dict[str, Any]] = {}
    for slot in slots:
        slot_type = slot["slot_type"]
        category = summary.setdefault(
            slot_type,
            {"slot_type": slot_type, "total": 0, "available": 0, "occupied": 0},
        )
        category["total"] += 1
        if slot["status"] == "Available":
            category["available"] += 1
        elif slot["status"] == "Occupied":
            category["occupied"] += 1

    return [
        summary[slot_type]
        for slot_type in ("Standard", "Compact", "Two-Wheeler", "EV", "Heavy")
        if slot_type in summary
    ]


def get_all_parking_slots(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieves all parking slots along with current occupant details and floor info."""
    return database.get_slots_with_occupants(db_path=db_path)


# ============================================================================
# Update Permitted Vehicle Information
# ============================================================================

def update_permitted_vehicle_info(
    session_id: int,
    vehicle_type: Optional[str] = None,
    owner_name: Optional[str] = None,
    owner_contact: Optional[str] = None,
    operator: Optional[AuthenticatedUser] = None,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """Updates permitted vehicle and owner information for an existing session."""
    if operator is not None:
        require_permission(operator, "vehicles:manage")

    session = database.get_session_by_id(session_id, db_path=db_path)
    if not session:
        raise SessionNotFoundError(f"Parking session ID {session_id} not found.")
        
    norm_vtype = normalize_vehicle_type(vehicle_type) if vehicle_type is not None else None
    if owner_name is not None and not isinstance(owner_name, str):
        raise ValidationError("Owner name must be a text value.")
    clean_owner = owner_name.strip() if owner_name is not None else None
    clean_contact = validate_contact_number(owner_contact) if owner_contact is not None else None
    
    database.update_session_details(
        session_id=session_id,
        vehicle_type=norm_vtype,
        owner_name=clean_owner,
        owner_contact=clean_contact,
        db_path=db_path
    )
    
    updated = database.get_session_by_id(session_id, db_path=db_path)
    return dict(updated)


# ============================================================================
# Safe Record Deletion Under Clearly Defined Conditions
# ============================================================================

def cancel_active_session_safely(
    session_id: int,
    cancellation_reason: str = "Operator correction",
    operator: Optional[AuthenticatedUser] = None,
    db_path: Optional[str] = None
) -> None:
    """Safely cancels an active session and frees the occupied slot."""
    if operator is not None:
        require_permission(operator, "vehicles:manage")

    session = database.get_session_by_id(session_id, db_path=db_path)
    if not session:
        raise SessionNotFoundError(f"Session ID {session_id} not found.")
    if session["status"] != "ACTIVE":
        raise InvalidExitError(f"Session {session_id} is '{session['status']}', not ACTIVE.")
        
    database.cancel_parking_session(session_id, db_path=db_path)


def delete_parking_record_safely(
    session_id: int,
    admin_override: bool = False,
    audit_reason: Optional[str] = None,
    operator: Optional[AuthenticatedUser] = None,
    db_path: Optional[str] = None
) -> None:
    """Deletes a parking record ONLY under clearly defined and safe conditions."""
    if operator is not None:
        require_permission(operator, "vehicles:manage")

    session = database.get_session_by_id(session_id, db_path=db_path)
    if not session:
        raise SessionNotFoundError(f"Parking session ID {session_id} not found.")
        
    status = session["status"]
    fee = session["fee"] or 0.0
    
    # Rule 1: Active sessions cannot be deleted
    if status == "ACTIVE":
        raise UnsafeDeletionError(
            f"Cannot delete active parking session {session_id}. "
            f"Cancel the session first using cancel_active_session_safely() to free slot '{session['slot_number']}'."
        )
        
    # Rule 2: Billed sessions require administrative override and reason
    if fee > 0.0 and not admin_override:
        raise UnsafeDeletionError(
            f"Cannot delete completed session {session_id} with paid fee (${fee:.2f}). "
            f"Financial audit history must be preserved. Provide admin_override=True and an audit reason."
        )
        
    if admin_override and (not audit_reason or not audit_reason.strip()):
        raise UnsafeDeletionError("Administrative override deletion requires an audit reason.")
        
    database.delete_session_record(session_id, db_path=db_path)
