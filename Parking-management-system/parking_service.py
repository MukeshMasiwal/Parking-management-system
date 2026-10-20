"""
Parking Management System - Business Logic Service Layer
=========================================================

This module implements the core business logic for the Parking Management System,
serving as the mediator between the Presentation Layer (GUI/CLI) and the
Data Access Layer (database.py).

Core Responsibilities:
1. Validation of vehicle registration numbers, vehicle types, and timestamps.
2. Capacity monitoring and intelligent slot allocation.
3. Preventing duplicate active sessions.
4. Fee calculation adhering to the approved Tiered Hourly Charging Policy.
5. Exit recording, duration computation, and atomic slot release.
6. Safe, audited record management and search.
7. Strict separation of concerns: ZERO raw SQL in this module.

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
import re
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple

import database
from database import DATETIME_FORMAT


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


# Supported vehicle classifications
SUPPORTED_VEHICLE_TYPES = ("Car", "Two-Wheeler", "Compact", "EV", "Heavy")


# ============================================================================
# Validation and Normalization Helpers
# ============================================================================

def validate_and_normalize_registration_number(registration_number: str) -> str:
    """
    Validates and standardizes a vehicle registration number.
    
    Validation Rules:
    - Must be a non-empty string.
    - Strips leading and trailing whitespace.
    - Converts to uppercase.
    - Length must be between 3 and 16 characters.
    - Must only contain uppercase letters, numbers, spaces, and hyphens.
    - Must contain at least 3 alphanumeric characters.
    
    :param registration_number: Raw user input plate string.
    :return: Sanitized and normalized registration string.
    :raises ValidationError: If input fails any validation rule.
    """
    if not registration_number or not isinstance(registration_number, str):
        raise ValidationError("Vehicle registration number must be a non-empty string.")
        
    cleaned = registration_number.strip().upper()
    if len(cleaned) < 3 or len(cleaned) > 16:
        raise ValidationError(
            f"Registration number '{cleaned}' length ({len(cleaned)}) is invalid. "
            f"Must be between 3 and 16 characters."
        )
        
    if not re.match(r"^[A-Z0-9\-\s]+$", cleaned):
        raise ValidationError(
            f"Registration number '{cleaned}' contains illegal characters. "
            f"Only alphanumeric characters, hyphens, and spaces are allowed."
        )
        
    alnum_only = re.sub(r"[^A-Z0-9]", "", cleaned)
    if len(alnum_only) < 3:
        raise ValidationError(
            f"Registration number '{cleaned}' must contain at least 3 alphanumeric characters."
        )
        
    return cleaned


def validate_contact_number(contact: Optional[str]) -> Optional[str]:
    """
    Validates optional owner contact number.
    If provided, must contain 7 to 20 characters and at least 7 digits.
    Permits digits, spaces, hyphens, parentheses, and leading plus.
    """
    if not contact:
        return None
    cleaned = str(contact).strip()
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
    digits_only = re.sub(r"[^0-9]", "", cleaned)
    if len(digits_only) < 7:
        raise ValidationError(
            f"Contact number '{cleaned}' must contain at least 7 digits."
        )
    return cleaned


def normalize_vehicle_type(vehicle_type: Optional[str]) -> str:
    """
    Normalizes vehicle type string to standard application classifications.
    Defaults to 'Car' if unspecified.
    """
    if not vehicle_type:
        return "Car"
        
    v = vehicle_type.strip().lower()
    if "two" in v or "bike" in v or "motorcycle" in v or "scooter" in v:
        return "Two-Wheeler"
    elif "compact" in v:
        return "Compact"
    elif "ev" in v or "electric" in v:
        return "EV"
    elif "heavy" in v or "truck" in v or "bus" in v:
        return "Heavy"
    elif "car" in v or "sedan" in v or "suv" in v or "standard" in v:
        return "Car"
    else:
        raise ValidationError(
            f"Unsupported vehicle type '{vehicle_type}'. "
            f"Allowed types: {', '.join(SUPPORTED_VEHICLE_TYPES)}"
        )


def validate_timestamp_string(ts_str: str, field_name: str = "Timestamp") -> datetime:
    """
    Validates and parses a datetime string supporting both standard and ISO formats.
    """
    if not ts_str or not isinstance(ts_str, str):
        raise ValidationError(f"{field_name} must be a valid datetime string.")
        
    fmt = DATETIME_FORMAT
    try:
        return datetime.strptime(ts_str.strip(), fmt)
    except ValueError:
        try:
            return datetime.fromisoformat(ts_str.strip())
        except ValueError:
            raise ValidationError(
                f"{field_name} '{ts_str}' is invalid. Expected format '{fmt}' or ISO-8601."
            )


# ============================================================================
# Fee & Duration Calculation Engine (Approved Policy)
# ============================================================================

def calculate_duration_and_fee(
    entry_time_str: str,
    exit_time_str: str,
    vehicle_type: str = "Car",
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Calculates stay duration and parking fee according to the approved policy:
    - 15-minute free grace period.
    - If stay <= 15 minutes, fee is $0.00.
    - If stay > 15 minutes:
      * Minimum charge of 1 hour.
      * Ceil rounding to the next full hour (e.g. 1 hr 05 mins -> 2 billable hours).
      * Rate loaded dynamically from application settings:
        - Two-Wheeler: $10.00/hr
        - Car / Compact / EV: $20.00/hr
        - Heavy: $40.00/hr
        
    :return: Dictionary containing elapsed minutes, billable hours, hourly rate, and fee.
    """
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
        fee = 0.0
    else:
        # Minimum 1 hour charge, rounded up to next full hour
        billable_hours = max(1, math.ceil(elapsed_minutes / 60.0))
        fee = round(billable_hours * hourly_rate, 2)
        
    hours_int = int(elapsed_minutes // 60)
    mins_int = int(elapsed_minutes % 60)
    duration_str = f"{hours_int}h {mins_int:02d}m"
    
    return {
        "duration_minutes": round(elapsed_minutes, 1),
        "duration_formatted": duration_str,
        "billable_hours": billable_hours,
        "hourly_rate": hourly_rate,
        "grace_period_mins": grace_period,
        "fee": fee
    }


# ============================================================================
# Vehicle Entry & Slot Allocation
# ============================================================================

def find_best_available_slot(
    vehicle_type: str,
    db_path: Optional[str] = None
) -> Optional[Any]:
    """
    Intelligently assigns the most suitable available slot based on vehicle type.
    
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
        available = database.get_available_slots(db_path=db_path, slot_type=preferred_type)
        if available:
            return available[0]
            
    # Final fallback: any available slot
    any_available = database.get_available_slots(db_path=db_path)
    return any_available[0] if any_available else None


def register_vehicle_entry(
    vehicle_registration_number: str,
    vehicle_type: str = "Car",
    owner_name: Optional[str] = None,
    owner_contact: Optional[str] = None,
    slot_id: Optional[int] = None,
    slot_number: Optional[str] = None,
    entry_time: Optional[str] = None,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Registers a vehicle entering the parking facility.
    
    Business Rules Enforced:
    1. Validates vehicle registration number (format, length, characters).
    2. Normalizes vehicle type to standard classification.
    3. Validates that vehicle has NO existing ACTIVE session (prevents duplicate entry).
    4. Checks facility capacity; raises ParkingCapacityFullError if facility is full.
    5. Allocates an available slot (either explicitly requested or automatically assigned).
    6. Executes atomic database entry (reserves slot and creates session).
    
    :return: Detailed dictionary of the registered session.
    """
    # 1. Validate registration plate
    clean_vno = validate_and_normalize_registration_number(vehicle_registration_number)
    clean_vtype = normalize_vehicle_type(vehicle_type)
    
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
        target_slot = database.get_slot_by_number(slot_number, db_path=db_path)
        if not target_slot:
            raise SlotNotFoundError(f"Slot number '{slot_number}' does not exist.")
        if target_slot["status"] != "Available":
            raise SlotNotAvailableError(
                f"Slot '{target_slot['slot_number']}' is currently '{target_slot['status']}', not Available."
            )
    else:
        # Auto-assignment
        target_slot = find_best_available_slot(clean_vtype, db_path=db_path)
        if not target_slot:
            # Check if facility is completely full
            all_available = database.get_available_slots(db_path=db_path)
            if not all_available:
                raise ParkingCapacityFullError("Parking facility is at 100% capacity. No slots available.")
            else:
                raise SlotNotAvailableError(f"No suitable parking slots currently available for {clean_vtype}.")
                
    chosen_slot_id = target_slot["id"]
    chosen_slot_number = target_slot["slot_number"]
    
    # 4. Atomic database insertion & slot reservation
    try:
        session_id = database.start_parking_session(
            vehicle_registration_number=clean_vno,
            slot_id=chosen_slot_id,
            vehicle_type=clean_vtype,
            owner_name=clean_owner_name,
            owner_contact=clean_owner_contact,
            entry_time=entry_ts,
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
        
    return {
        "session_id": session_id,
        "vehicle_registration_number": clean_vno,
        "vehicle_type": clean_vtype,
        "owner_name": clean_owner_name,
        "owner_contact": clean_owner_contact,
        "slot_id": chosen_slot_id,
        "slot_number": chosen_slot_number,
        "entry_time": entry_ts,
        "status": "ACTIVE"
    }


# ============================================================================
# Vehicle Exit, Fee Billing & Slot Release
# ============================================================================

def record_vehicle_exit(
    session_id: Optional[int] = None,
    vehicle_registration_number: Optional[str] = None,
    exit_time: Optional[str] = None,
    discount_amount: float = 0.0,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Processes vehicle exit: calculates duration and fee, releases slot, and preserves history.
    
    Guarantees:
    - Exit timestamp must be chronological (exit >= entry). If invalid, raises InvalidExitError
      BEFORE database modification so the slot is NOT released.
    - Slot release and session completion occur within an atomic database transaction.
    - If the transaction fails, the slot remains Occupied.
    - Completed record is permanently preserved with status 'COMPLETED'.
    
    :param session_id: Primary key of active session, or
    :param vehicle_registration_number: Plate number of active vehicle.
    :param exit_time: Optional exit timestamp (defaults to current time).
    :param discount_amount: Optional operator discount (cannot exceed fee).
    :return: Exit invoice and receipt dictionary.
    """
    if not isinstance(discount_amount, (int, float)) or isinstance(discount_amount, bool):
        raise ValidationError("Discount amount must be a non-negative number.")
    if discount_amount < 0:
        raise ValidationError("Discount amount cannot be negative.")

    # 1. Identify active session
    session = None
    if session_id is not None:
        session = database.get_session_by_id(session_id, db_path=db_path)
    elif vehicle_registration_number is not None:
        clean_vno = validate_and_normalize_registration_number(vehicle_registration_number)
        session = database.get_active_session_by_vehicle(clean_vno, db_path=db_path)
    else:
        raise ValidationError("Either session_id or vehicle_registration_number must be provided.")
        
    if not session:
        identifier = f"ID {session_id}" if session_id else f"Vehicle '{vehicle_registration_number}'"
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
        db_path=db_path
    )
    
    base_fee = fee_calc["fee"]
    if discount_amount > base_fee:
        raise ValidationError(
            f"Discount amount ({discount_amount:.2f}) cannot exceed the calculated fee ({base_fee:.2f})."
        )
    final_fee = max(0.0, round(base_fee - discount_amount, 2))
    
    # 3. Perform atomic exit and slot release in database
    try:
        updated = database.end_parking_session(
            session_id=session["id"],
            exit_time=exit_ts,
            fee=final_fee,
            db_path=db_path
        )
    except database.SessionNotFoundError as e:
        raise SessionNotFoundError(str(e)) from e
    except database.InvalidOperationError as e:
        raise InvalidExitError(str(e)) from e
    except database.DatabaseError as e:
        raise InvalidExitError(f"Database error during exit processing: {e}") from e
        
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
        "status": "COMPLETED"
    }


# ============================================================================
# Search and Record Retrieval
# ============================================================================

def search_parking_records(
    keyword: Optional[str] = None,
    status: Optional[str] = None,
    vehicle_type: Optional[str] = None,
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
    """
    Retrieves all currently active parking sessions with current duration estimates.
    """
    return search_parking_records(status="ACTIVE", db_path=db_path)


def get_vehicle_history(
    vehicle_registration_number: str,
    limit: int = 100,
    db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Retrieves full historical parking sessions for a specific vehicle registration.
    """
    clean_vno = validate_and_normalize_registration_number(vehicle_registration_number)
    rows = database.get_session_history(vehicle_number=clean_vno, limit=limit, db_path=db_path)
    return [dict(r) for r in rows]


def get_parking_capacity_status(db_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Retrieves facility capacity and real-time occupancy statistics, including recorded revenue.
    """
    return database.get_capacity_metrics(db_path=db_path)


def get_dashboard_metrics(db_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Retrieves high-level summary KPIs and recorded revenue for the Dashboard.
    """
    return get_parking_capacity_status(db_path=db_path)


def get_all_parking_slots(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Retrieves all parking slots along with current occupant details.
    """
    return database.get_slots_with_occupants(db_path=db_path)


# ============================================================================
# Update Permitted Vehicle Information
# ============================================================================

def update_permitted_vehicle_info(
    session_id: int,
    vehicle_type: Optional[str] = None,
    owner_name: Optional[str] = None,
    owner_contact: Optional[str] = None,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Updates permitted vehicle and owner information for an existing session.
    
    Permitted fields:
    - vehicle_type (normalized)
    - owner_name
    - owner_contact
    
    Guards:
    - Immutable fields (slot_id, entry_time, exit_time, status, fee) cannot be altered.
    """
    session = database.get_session_by_id(session_id, db_path=db_path)
    if not session:
        raise SessionNotFoundError(f"Parking session ID {session_id} not found.")
        
    norm_vtype = normalize_vehicle_type(vehicle_type) if vehicle_type else None
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
    db_path: Optional[str] = None
) -> None:
    """
    Safely cancels an active session and frees the occupied slot.
    Use this when an operator logs an entry in error.
    """
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
    db_path: Optional[str] = None
) -> None:
    """
    Deletes a parking record ONLY under clearly defined and safe conditions.
    
    Strict Safety Rules:
    1. ACTIVE sessions CANNOT be deleted. They must be cancelled or exited first.
    2. Billed COMPLETED sessions (fee > 0) CANNOT be deleted without admin_override
       and an audit reason, protecting financial compliance.
    3. Safe deletion is permitted for:
       - CANCELLED sessions (logged in error, 0 fee).
       - Zero-fee completed sessions (e.g. grace period exits with 0 charge).
       - Administrative audit deletions with recorded reason.
       
    :raises UnsafeDeletionError: If attempted deletion violates safety rules.
    """
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
        
    # Condition satisfied -> Delete low-level record
    database.delete_session_record(session_id, db_path=db_path)
