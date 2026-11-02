"""
Multi-User Authentication and Role-Based Access Control (RBAC) Layer
====================================================================

This module implements enterprise security, bcrypt password hashing,
user session management, granular permission evaluation, and user administration
for the Parking Management System across five defined roles:
1. ADMIN
2. MANAGER
3. CASHIER
4. SECURITY
5. VALET

Strict Security Rule:
Hiding a button is not sufficient. Every sensitive operational routine
validates business permissions before execution.
"""

from dataclasses import dataclass, field
import hashlib
import hmac
import secrets
import sqlite3
from typing import Optional, List, Dict, Any, Tuple

try:
    import bcrypt
    _HAS_BCRYPT = True
except ImportError:
    _HAS_BCRYPT = False

import database


# ============================================================================
# Custom Security Exceptions
# ============================================================================

class AuthenticationError(Exception):
    """Base exception for authentication failures."""
    pass


class InvalidCredentialsError(AuthenticationError):
    """Raised when authentication credentials do not match."""
    pass


class AccountInactiveError(AuthenticationError):
    """Raised when an inactive or suspended user attempts to sign in."""
    pass


class PermissionDeniedError(AuthenticationError):
    """Raised when a user attempts an action for which they lack permissions."""
    pass


class UserManagementError(AuthenticationError):
    """Raised when user creation, updating, or deactivation fails."""
    pass


# ============================================================================
# Authenticated User Data Model
# ============================================================================

@dataclass(frozen=True)
class AuthenticatedUser:
    """Represents an active, authenticated user session."""
    username: str
    role: str
    user_id: Optional[int] = None
    full_name: str = ""
    email: Optional[str] = None
    permissions: Tuple[str, ...] = field(default_factory=tuple)
    is_active: bool = True

    def has_permission(self, permission_code: str) -> bool:
        """Convenience method to check permission on the user instance."""
        return has_permission(self, permission_code)

    def is_admin(self) -> bool:
        return self.role.upper() == "ADMIN"

    def is_manager(self) -> bool:
        return self.role.upper() in ("MANAGER", "ADMIN")

    def is_cashier(self) -> bool:
        return self.role.upper() == "CASHIER"

    def is_security(self) -> bool:
        return self.role.upper() == "SECURITY"

    def is_valet(self) -> bool:
        return self.role.upper() == "VALET"


# ============================================================================
# Password Hashing & Verification Engine
# ============================================================================

def hash_password(raw_password: str) -> str:
    """
    Hashes a plaintext password using bcrypt with a salt.
    Falls back to PBKDF2-HMAC-SHA256 if bcrypt is unavailable.
    """
    if not isinstance(raw_password, str) or not raw_password:
        raise ValueError("Password must be a non-empty string.")

    if _HAS_BCRYPT:
        salt = bcrypt.gensalt(rounds=10)
        return bcrypt.hashpw(raw_password.encode("utf-8"), salt).decode("utf-8")
    else:
        # Fallback PBKDF2
        salt = secrets.token_hex(16)
        kdf = hashlib.pbkdf2_hmac("sha256", raw_password.encode("utf-8"), salt.encode("utf-8"), 100000)
        return f"pbkdf2_sha256$100000${salt}${kdf.hex()}"


def verify_password(raw_password: str, hashed_password: str) -> bool:
    """
    Verifies a plaintext password against a stored hash string.
    Supports bcrypt, PBKDF2, and legacy plaintext compare fallback.
    """
    if not raw_password or not hashed_password:
        return False

    raw_bytes = raw_password.encode("utf-8")

    # 1. Bcrypt verification
    if hashed_password.startswith(("$2a$", "$2b$", "$2y$")):
        if _HAS_BCRYPT:
            try:
                return bcrypt.checkpw(raw_bytes, hashed_password.encode("utf-8"))
            except Exception:
                return False
        return False

    # 2. PBKDF2 verification
    if hashed_password.startswith("pbkdf2_sha256$"):
        parts = hashed_password.split("$")
        if len(parts) == 4:
            _, rounds_str, salt, target_hex = parts
            computed = hashlib.pbkdf2_hmac(
                "sha256", raw_bytes, salt.encode("utf-8"), int(rounds_str)
            ).hex()
            return hmac.compare_digest(computed, target_hex)

    # 3. Constant-time plaintext check (legacy/demo fallback)
    return hmac.compare_digest(raw_password, hashed_password)


# ============================================================================
# Authentication Engine
# ============================================================================

def authenticate(
    username: str,
    password: str,
    db_path: Optional[str] = None
) -> Optional[AuthenticatedUser]:
    """
    Authenticates a user against the database users table.
    Enforces active account status, verifies password hash,
    loads assigned role permissions, logs sign-in activity,
    and returns an AuthenticatedUser instance.
    """
    clean_username = username.strip().lower() if isinstance(username, str) else ""
    raw_password = password if isinstance(password, str) else ""
    if not clean_username or not raw_password:
        return None

    # First attempt database authentication
    try:
        user_row = database.get_user_by_username(clean_username, db_path=db_path)
        if user_row:
            # Check account active status
            if not user_row["is_active"]:
                database.log_activity_event(
                    user_id=user_row["id"],
                    username=clean_username,
                    action="LOGIN_BLOCKED_INACTIVE",
                    entity_type="USER",
                    entity_id=str(user_row["id"]),
                    details="Attempted login on inactive/deactivated account",
                    db_path=db_path
                )
                return None

            # Verify password hash
            if not verify_password(raw_password, user_row["password_hash"]):
                database.log_activity_event(
                    user_id=user_row["id"],
                    username=clean_username,
                    action="LOGIN_FAILED_BAD_PASSWORD",
                    entity_type="USER",
                    entity_id=str(user_row["id"]),
                    details="Invalid password attempt",
                    db_path=db_path
                )
                return None

            # Retrieve mapped permissions for role
            role_name = user_row["role_name"].upper()
            perms = tuple(database.get_permissions_for_role(role_name, db_path=db_path))

            # Record login timestamp & audit event
            database.record_user_login(user_row["id"], db_path=db_path)
            database.log_activity_event(
                user_id=user_row["id"],
                username=clean_username,
                action="USER_LOGIN_SUCCESS",
                entity_type="USER",
                entity_id=str(user_row["id"]),
                details=f"Successful login with role '{role_name}'",
                db_path=db_path
            )

            return AuthenticatedUser(
                username=clean_username,
                role=role_name,
                user_id=user_row["id"],
                full_name=user_row["full_name"],
                email=user_row["email"],
                permissions=perms,
                is_active=True
            )
    except (sqlite3.Error, OSError) as exc:
        raise AuthenticationError("Authentication storage is unavailable.") from exc

    return None


# ============================================================================
# Permission Evaluation & Enforcement
# ============================================================================

def has_permission(user: Optional[AuthenticatedUser], permission_code: str) -> bool:
    """
    Evaluates whether an authenticated user is authorized for a permission.
    Admins hold unrestricted system access across all codes.
    """
    if user is None or not user.is_active:
        return False

    role_upper = user.role.upper()
    if role_upper == "ADMIN":
        return True

    return permission_code in user.permissions


def require_permission(user: Optional[AuthenticatedUser], permission_code: str) -> None:
    """
    Enforces that a user holds the required permission.
    Raises PermissionDeniedError immediately if unauthorized.
    """
    if not has_permission(user, permission_code):
        uname = getattr(user, "username", "Anonymous")
        urole = getattr(user, "role", "None")
        raise PermissionDeniedError(
            f"Access Denied: User '{uname}' with role '{urole}' lacks permission '{permission_code}'."
        )


# Backward-compatible convenience checks
def can_manage_settings(user: Optional[AuthenticatedUser]) -> bool:
    """Return whether a user may change billing rates and developer settings."""
    return has_permission(user, "settings:manage")


def can_delete_records(user: Optional[AuthenticatedUser]) -> bool:
    """Return whether a user may permanently delete parking records."""
    return has_permission(user, "vehicles:manage")


# ============================================================================
# User Administration Operations (RBAC Enforced)
# ============================================================================

def create_user(
    operator: AuthenticatedUser,
    username: str,
    password: str,
    role_name: str,
    full_name: str,
    email: Optional[str] = None,
    db_path: Optional[str] = None
) -> int:
    """
    Creates a new user record.
    Operator must possess 'users:manage' permission.
    """
    require_permission(operator, "users:manage")

    role_upper = role_name.strip().upper()
    valid_roles = ("ADMIN", "MANAGER", "CASHIER", "SECURITY", "VALET")
    if role_upper not in valid_roles:
        raise UserManagementError(f"Role '{role_name}' is invalid. Must be one of {valid_roles}")

    pwd_hash = hash_password(password)
    user_id = database.create_user_record(
        username=username,
        password_hash=pwd_hash,
        full_name=full_name,
        role_name=role_upper,
        email=email,
        is_active=1,
        db_path=db_path
    )

    database.log_activity_event(
        user_id=operator.user_id,
        username=operator.username,
        action="USER_CREATED",
        entity_type="USER",
        entity_id=str(user_id),
        details=f"Created user '{username}' with role '{role_upper}'",
        db_path=db_path
    )
    return user_id


def update_user_profile(
    operator: AuthenticatedUser,
    user_id: int,
    full_name: Optional[str] = None,
    email: Optional[str] = None,
    role_name: Optional[str] = None,
    is_active: Optional[int] = None,
    db_path: Optional[str] = None
) -> None:
    """
    Updates user details or changes active status.
    Operator must possess 'users:manage' permission.
    """
    require_permission(operator, "users:manage")

    target = database.get_user_by_id(user_id, db_path=db_path)
    if not target:
        raise UserManagementError(f"Target user with ID {user_id} not found.")

    # Guard: Non-admin cannot modify an administrator account
    if target["role_name"].upper() == "ADMIN" and operator.role.upper() != "ADMIN":
        raise PermissionDeniedError("Only Administrators can modify Administrator accounts.")

    database.update_user_record(
        user_id=user_id,
        full_name=full_name,
        email=email,
        role_name=role_name,
        is_active=is_active,
        db_path=db_path
    )

    database.log_activity_event(
        user_id=operator.user_id,
        username=operator.username,
        action="USER_UPDATED",
        entity_type="USER",
        entity_id=str(user_id),
        details=f"Updated user ID {user_id}",
        db_path=db_path
    )


def reset_user_password(
    operator: AuthenticatedUser,
    user_id: int,
    new_password: str,
    db_path: Optional[str] = None
) -> None:
    """
    Resets a user's password securely with bcrypt hashing.
    Operator must possess 'users:reset_password' permission.
    """
    require_permission(operator, "users:reset_password")

    target = database.get_user_by_id(user_id, db_path=db_path)
    if not target:
        raise UserManagementError(f"Target user with ID {user_id} not found.")

    if target["role_name"].upper() == "ADMIN" and operator.role.upper() != "ADMIN":
        raise PermissionDeniedError("Only Administrators can reset Administrator passwords.")

    pwd_hash = hash_password(new_password)
    database.update_user_password(user_id, pwd_hash, db_path=db_path)

    database.log_activity_event(
        user_id=operator.user_id,
        username=operator.username,
        action="USER_PASSWORD_RESET",
        entity_type="USER",
        entity_id=str(user_id),
        details=f"Password reset for user '{target['username']}'",
        db_path=db_path
    )


def deactivate_user(
    operator: AuthenticatedUser,
    user_id: int,
    db_path: Optional[str] = None
) -> None:
    """Deactivates a user account."""
    require_permission(operator, "users:manage")
    if operator.user_id == user_id:
        raise UserManagementError("You cannot deactivate your own account.")

    update_user_profile(operator, user_id, is_active=0, db_path=db_path)


def activate_user(
    operator: AuthenticatedUser,
    user_id: int,
    db_path: Optional[str] = None
) -> None:
    """Activates a deactivated user account."""
    require_permission(operator, "users:manage")
    update_user_profile(operator, user_id, is_active=1, db_path=db_path)


def list_users(
    operator: AuthenticatedUser,
    db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Retrieves list of users for administration."""
    require_permission(operator, "users:manage")
    rows = database.get_all_users(db_path=db_path)
    return [dict(r) for r in rows]
