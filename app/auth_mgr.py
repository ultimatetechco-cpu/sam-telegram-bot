import hashlib
import json
import math
import os
import secrets
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional

from app.config import DATA_DIR, ensure_directories

USERS_FILE = DATA_DIR / "users.json"
SESSIONS_FILE = DATA_DIR / "auth_sessions.json"

# Monthly Subscription Tiers
PLANS = {
    "tier_1": {
        "id": "tier_1",
        "name": "Starter Plan",
        "price": 10,
        "max_accounts": 1,
        "description": "$10/month - 1 Telegram Account",
        "features": ["1 Telegram Account", "Unlimited Target Groups", "3D Animated Emojis", "Anti-Flood Automation"]
    },
    "tier_2": {
        "id": "tier_2",
        "name": "Growth Plan",
        "price": 20,
        "max_accounts": 2,
        "description": "$20/month - 2 Telegram Accounts",
        "features": ["2 Telegram Accounts", "Multi-Account Rotation", "Unlimited Target Groups", "3D Animated Emojis"]
    },
    "tier_3": {
        "id": "tier_3",
        "name": "Pro Plan",
        "price": 30,
        "max_accounts": 3,
        "description": "$30/month - 3 Telegram Accounts",
        "features": ["3 Telegram Accounts", "Smart Load Balancing", "Multi-Post Campaign Pool", "Full Priority Support"]
    },
    "unlimited": {
        "id": "unlimited",
        "name": "Admin Unlimited",
        "price": 0,
        "max_accounts": 999,
        "description": "Unlimited Accounts & Full Platform Access",
        "features": ["Unlimited Accounts", "Full Admin Dashboard", "User Management & Subscriptions", "Platform Analytics"]
    }
}

ACTIVE_TOKENS: Dict[str, Dict[str, Any]] = {}

def hash_password(password: str, salt: Optional[str] = None) -> (str, str):
    if not salt:
        salt = secrets.token_hex(16)
    hashed = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return hashed, salt

def verify_password(password: str, hashed: str, salt: str) -> bool:
    calc, _ = hash_password(password, salt)
    return secrets.compare_digest(calc, hashed)

def load_users() -> List[Dict[str, Any]]:
    ensure_directories()
    users = []
    if USERS_FILE.exists():
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                users = json.load(f)
        except Exception:
            users = []
    
    # Always guarantee super admin exists in users
    has_admin = any(u.get("role") == "admin" for u in users)
    if not has_admin:
        admin_hash, admin_salt = hash_password("admin123")
        now_dt = datetime.now()
        admin_user = {
            "id": "user_admin",
            "username": "admin",
            "email": "simardeep647.ss@gmail.com",
            "password_hash": admin_hash,
            "salt": admin_salt,
            "role": "admin",
            "status": "active", # active, pending, expired, suspended
            "plan_tier": "unlimited",
            "max_accounts": 999,
            "subscription_price": 0,
            "subscription_start": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "subscription_end": (now_dt + timedelta(days=3650)).strftime("%Y-%m-%d %H:%M:%S"),
            "created_at": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "notes": "System Super Administrator"
        }
        users.insert(0, admin_user)
        try:
            with open(USERS_FILE, "w", encoding="utf-8") as f:
                json.dump(users, f, indent=2, ensure_ascii=False)
        except Exception:
            pass
    return users

def save_users(users: List[Dict[str, Any]]) -> None:
    ensure_directories()
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2, ensure_ascii=False)

def load_auth_sessions() -> Dict[str, Dict[str, Any]]:
    global ACTIVE_TOKENS
    if SESSIONS_FILE.exists():
        try:
            with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
                ACTIVE_TOKENS = json.load(f)
                return ACTIVE_TOKENS
        except Exception:
            pass
    return ACTIVE_TOKENS

def save_auth_sessions() -> None:
    ensure_directories()
    try:
        with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(ACTIVE_TOKENS, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

def init_auth():
    """Initializes users database and seeds default admin if empty or missing admin."""
    ensure_directories()
    load_auth_sessions()
    users = load_users()
    has_admin = any(u.get("role") == "admin" for u in users)
    if not has_admin:
        # Seed default Admin
        admin_hash, admin_salt = hash_password("admin123")
        now_dt = datetime.now()
        admin_user = {
            "id": "user_admin",
            "username": "admin",
            "email": "simardeep647.ss@gmail.com",
            "password_hash": admin_hash,
            "salt": admin_salt,
            "role": "admin",
            "status": "active", # active, pending, expired, suspended
            "plan_tier": "unlimited",
            "max_accounts": 999,
            "subscription_price": 0,
            "subscription_start": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "subscription_end": (now_dt + timedelta(days=3650)).strftime("%Y-%m-%d %H:%M:%S"),
            "created_at": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "notes": "System Super Administrator"
        }
        users.append(admin_user)
        save_users(users)

# Auto-seed on load
try:
    init_auth()
except Exception:
    pass

def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    users = load_users()
    for u in users:
        if u["id"] == user_id:
            return update_subscription_status(u)
    return None

def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    users = load_users()
    for u in users:
        if u["username"].lower() == username.lower().strip():
            return update_subscription_status(u)
    return None

def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    users = load_users()
    for u in users:
        if u.get("email", "").lower() == email.lower().strip():
            return update_subscription_status(u)
    return None

def update_subscription_status(user: Dict[str, Any]) -> Dict[str, Any]:
    """Calculates days remaining and auto-locks if expired."""
    if user.get("role") == "admin":
        user["days_remaining"] = 9999
        user["is_locked"] = False
        return user

    end_str = user.get("subscription_end")
    if not end_str:
        user["days_remaining"] = 0
        user["is_locked"] = True
        if user.get("status") == "active":
            user["status"] = "expired"
        return user

    try:
        end_dt = datetime.strptime(end_str, "%Y-%m-%d %H:%M:%S")
        now_dt = datetime.now()
        remaining = (end_dt - now_dt).total_seconds()
        days_rem = max(0, int(math.ceil(remaining / 86400.0))) if remaining > 0 else 0
        user["days_remaining"] = days_rem

        if remaining <= 0:
            if user.get("status") == "active":
                user["status"] = "expired"
                # Persist updated status
                users = load_users()
                for i, u in enumerate(users):
                    if u["id"] == user["id"]:
                        users[i]["status"] = "expired"
                        save_users(users)
                        break
            user["is_locked"] = True
        else:
            user["is_locked"] = (user.get("status") != "active")
    except Exception:
        user["days_remaining"] = 0
        user["is_locked"] = True

    return user

def register_user(username: str, email: str, password: str, plan_tier: str = "tier_1") -> Dict[str, Any]:
    username = username.strip()
    email = email.strip()
    if not username or len(username) < 3:
        raise ValueError("Username must be at least 3 characters.")
    if not password or len(password) < 6:
        raise ValueError("Password must be at least 6 characters.")
    if get_user_by_username(username):
        raise ValueError("Username is already taken. Please choose another.")
    if get_user_by_email(email):
        raise ValueError("Email is already registered. Please log in.")

    tier_info = PLANS.get(plan_tier, PLANS["tier_1"])
    h, s = hash_password(password)
    user_id = f"user_{int(time.time())}_{secrets.token_hex(3)}"
    now_dt = datetime.now()

    new_user = {
        "id": user_id,
        "username": username,
        "email": email,
        "password_hash": h,
        "salt": s,
        "role": "user",
        "status": "pending", # Requires Admin approval
        "plan_tier": tier_info["id"],
        "max_accounts": tier_info["max_accounts"],
        "subscription_price": tier_info["price"],
        "subscription_start": None,
        "subscription_end": None,
        "created_at": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "notes": f"Registered for {tier_info['name']} (${tier_info['price']}/mo - {tier_info['max_accounts']} Account)"
    }

    users = load_users()
    users.append(new_user)
    save_users(users)

    token = secrets.token_urlsafe(32)
    session_data = {
        "token": token,
        "user_id": new_user["id"],
        "username": new_user["username"],
        "role": new_user["role"],
        "login_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    ACTIVE_TOKENS[token] = session_data
    save_auth_sessions()

    return {
        "token": token,
        "user": sanitize_user(new_user)
    }

def login_user(username: str, password: str) -> Dict[str, Any]:
    user = get_user_by_username(username)
    if not user:
        user = get_user_by_email(username)
    if not user:
        raise ValueError("Invalid username or password.")

    if not verify_password(password, user["password_hash"], user["salt"]):
        raise ValueError("Invalid username or password.")

    token = secrets.token_urlsafe(32)
    session_data = {
        "token": token,
        "user_id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "login_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    ACTIVE_TOKENS[token] = session_data
    save_auth_sessions()

    return {
        "token": token,
        "user": sanitize_user(user)
    }

def logout_user(token: str) -> None:
    if token in ACTIVE_TOKENS:
        del ACTIVE_TOKENS[token]
        save_auth_sessions()

def authenticate_token(token: Optional[str]) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    session = ACTIVE_TOKENS.get(token)
    if not session:
        # Try reloading sessions from disk
        load_auth_sessions()
        session = ACTIVE_TOKENS.get(token)
    if not session:
        return None

    user = get_user_by_id(session["user_id"])
    return user

def sanitize_user(user: Dict[str, Any]) -> Dict[str, Any]:
    u = update_subscription_status(dict(user))
    u.pop("password_hash", None)
    u.pop("salt", None)
    plan_info = PLANS.get(u.get("plan_tier"), PLANS["tier_1"])
    u["plan_info"] = plan_info
    return u

# --- Admin Operations ---

def admin_approve_user(user_id: str, plan_tier: str = "tier_1", duration_days: int = 30) -> Dict[str, Any]:
    users = load_users()
    for i, u in enumerate(users):
        if u["id"] == user_id:
            tier_info = PLANS.get(plan_tier, PLANS["tier_1"])
            now_dt = datetime.now()
            end_dt = now_dt + timedelta(days=duration_days)

            users[i]["status"] = "active"
            users[i]["plan_tier"] = tier_info["id"]
            users[i]["max_accounts"] = tier_info["max_accounts"]
            users[i]["subscription_price"] = tier_info["price"]
            users[i]["subscription_start"] = now_dt.strftime("%Y-%m-%d %H:%M:%S")
            users[i]["subscription_end"] = end_dt.strftime("%Y-%m-%d %H:%M:%S")
            users[i]["notes"] = f"Approved by Admin on {now_dt.strftime('%Y-%m-%d')}"
            save_users(users)
            return sanitize_user(users[i])
    raise ValueError(f"User {user_id} not found.")

def admin_extend_subscription(user_id: str, extra_days: int = 30) -> Dict[str, Any]:
    users = load_users()
    for i, u in enumerate(users):
        if u["id"] == user_id:
            now_dt = datetime.now()
            cur_end = u.get("subscription_end")
            if cur_end:
                try:
                    base_dt = datetime.strptime(cur_end, "%Y-%m-%d %H:%M:%S")
                    if base_dt < now_dt:
                        base_dt = now_dt
                except Exception:
                    base_dt = now_dt
            else:
                base_dt = now_dt

            new_end = base_dt + timedelta(days=extra_days)
            users[i]["status"] = "active"
            if not users[i].get("subscription_start"):
                users[i]["subscription_start"] = now_dt.strftime("%Y-%m-%d %H:%M:%S")
            users[i]["subscription_end"] = new_end.strftime("%Y-%m-%d %H:%M:%S")
            save_users(users)
            return sanitize_user(users[i])
    raise ValueError(f"User {user_id} not found.")

def admin_update_user_plan(user_id: str, plan_tier: str) -> Dict[str, Any]:
    if plan_tier not in PLANS:
        raise ValueError(f"Invalid plan tier. Choose from {list(PLANS.keys())}")
    tier_info = PLANS[plan_tier]
    users = load_users()
    for i, u in enumerate(users):
        if u["id"] == user_id:
            users[i]["plan_tier"] = tier_info["id"]
            users[i]["max_accounts"] = tier_info["max_accounts"]
            users[i]["subscription_price"] = tier_info["price"]
            save_users(users)
            return sanitize_user(users[i])
    raise ValueError(f"User {user_id} not found.")

def admin_set_user_status(user_id: str, status: str) -> Dict[str, Any]:
    if status not in ["active", "pending", "expired", "suspended"]:
        raise ValueError("Status must be active, pending, expired, or suspended.")
    users = load_users()
    for i, u in enumerate(users):
        if u["id"] == user_id:
            users[i]["status"] = status
            save_users(users)
            return sanitize_user(users[i])
    raise ValueError(f"User {user_id} not found.")

def admin_delete_user(user_id: str) -> bool:
    users = load_users()
    initial_len = len(users)
    users = [u for u in users if u["id"] != user_id and u.get("role") != "admin"]
    if len(users) < initial_len:
        save_users(users)
        # Clean user directory
        user_dir = DATA_DIR / "users" / user_id
        if user_dir.exists():
            import shutil
            shutil.rmtree(user_dir, ignore_errors=True)
        return True
    return False

def get_all_users_for_admin() -> List[Dict[str, Any]]:
    users = load_users()
    return [sanitize_user(u) for u in users]

def admin_change_password(user_id: str, new_password: str) -> bool:
    if len(new_password) < 6:
        raise ValueError("New password must be at least 6 characters.")
    users = load_users()
    found = False
    for i, u in enumerate(users):
        if u["id"] == user_id:
            h, s = hash_password(new_password)
            users[i]["password_hash"] = h
            users[i]["salt"] = s
            save_users(users)
            found = True
            break
    return found

def admin_update_email(user_id: str, new_email: str) -> Dict[str, Any]:
    new_email = new_email.strip().lower()
    if "@" not in new_email:
        raise ValueError("Invalid email address format.")
    users = load_users()
    # Check if another user already has this email
    for u in users:
        if u["id"] != user_id and u.get("email", "").lower() == new_email:
            raise ValueError("Email already in use by another account.")
    for i, u in enumerate(users):
        if u["id"] == user_id:
            users[i]["email"] = new_email
            save_users(users)
            return sanitize_user(users[i])
    raise ValueError("User not found.")
