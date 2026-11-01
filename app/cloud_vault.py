import asyncio
import json
import logging
import os
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List

from telethon.sessions import StringSession, SQLiteSession

from app.config import DATA_DIR, SESSIONS_DIR, ensure_directories
from app.storage import (
    get_accounts,
    save_accounts,
    get_posts,
    save_posts,
    get_groups,
    save_groups,
    load_config,
    save_config,
    add_log
)
from app.auth_mgr import (
    load_users,
    save_users,
    load_auth_sessions,
    save_auth_sessions
)

logger = logging.getLogger("cloud_vault")

HOSTINGER_VAULT_URL = "https://omeglevideochat.online/telegrambot/api_proxy.php"
VAULT_SECRET = "sam_telegram_autopost_live_sync_2026_x99"
RENDER_APP_URL = "https://sam-telegram-bot-hgm1.onrender.com"

_sync_lock = asyncio.Lock()
_pending_push: bool = False
_is_restoring: bool = False

def write_sqlite_session_from_string(session_name: str, string_sess_str: str) -> bool:
    """
    Directly reconstructs the Telethon SQLite .session file from a portable StringSession.
    Runs instantaneously without any network request.
    """
    if not session_name or not string_sess_str:
        return False
    try:
        ensure_directories()
        session_file = SESSIONS_DIR / f"{session_name}.session"
        if session_file.exists() and session_file.stat().st_size > 0:
            return True

        str_sess = StringSession(string_sess_str.strip())
        if not str_sess.auth_key:
            return False

        sql_sess = SQLiteSession(str(SESSIONS_DIR / session_name))
        if str_sess.dc_id:
            sql_sess.set_dc(str_sess.dc_id, str_sess.server_address, str_sess.port)
        sql_sess.auth_key = str_sess.auth_key
        sql_sess.save()
        logger.info(f"Reconstructed SQLite session: {session_name}.session")
        return True
    except Exception as e:
        logger.warning(f"Failed to reconstruct SQLite session for {session_name}: {e}")
        return False

def get_string_session_from_disk(session_name: str) -> Optional[str]:
    """
    Extracts a portable StringSession from an existing SQLite .session file on disk.
    """
    if not session_name:
        return None
    try:
        session_file = SESSIONS_DIR / f"{session_name}.session"
        if not session_file.exists():
            return None
        sql_sess = SQLiteSession(str(SESSIONS_DIR / session_name))
        if not sql_sess.auth_key:
            return None
        str_sess = StringSession()
        str_sess.set_dc(sql_sess.dc_id, sql_sess.server_address, sql_sess.port)
        str_sess.auth_key = sql_sess.auth_key
        return str_sess.save()
    except Exception as e:
        logger.warning(f"Could not read string session from {session_name}: {e}")
        return None

def _enrich_accounts_with_string_sessions(accounts: List[Dict[str, Any]], user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Ensures every account has a string_session populated from disk if missing.
    """
    modified = False
    for acc in accounts:
        if not acc.get("string_session"):
            session_name = acc.get("session_name")
            if session_name:
                extracted = get_string_session_from_disk(session_name)
                if extracted:
                    acc["string_session"] = extracted
                    modified = True
    if modified:
        save_accounts(accounts, user_id=user_id, sync_vault=False)
    return accounts

def build_vault_payload() -> Dict[str, Any]:
    """
    Constructs a comprehensive snapshot of all tenant users, web auth tokens,
    accounts (with string sessions), posts, groups, and configurations for permanent storage.
    """
    ensure_directories()
    users = load_users()
    auth_sessions = load_auth_sessions()
    user_data = {}

    for u in users:
        uid = u["id"]
        if uid in ["admin", "user_admin"]:
            continue
        user_accounts = _enrich_accounts_with_string_sessions(get_accounts(uid), user_id=uid)
        user_data[uid] = {
            "accounts": user_accounts,
            "posts": get_posts(uid),
            "groups": get_groups(uid),
            "config": load_config(uid)
        }

    admin_accounts = _enrich_accounts_with_string_sessions(get_accounts("admin"), user_id="admin")
    admin_data = {
        "accounts": admin_accounts,
        "posts": get_posts("admin"),
        "groups": get_groups("admin"),
        "config": load_config("admin")
    }

    return {
        "version": "2.0",
        "synced_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "users": users,
        "auth_sessions": auth_sessions,
        "user_data": user_data,
        "admin_data": admin_data
    }

async def push_vault_state() -> bool:
    """
    Uploads the current tenant state to Hostinger's persistent storage.
    Runs asynchronously and non-blocking.
    """
    global _pending_push, _is_restoring
    if _is_restoring:
        return False

    try:
        payload = build_vault_payload()
        json_data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        url = f"{HOSTINGER_VAULT_URL}?action=vault_push&secret={VAULT_SECRET}"
        req = urllib.request.Request(
            url,
            data=json_data,
            headers={
                "Content-Type": "application/json",
                "X-Vault-Secret": VAULT_SECRET,
                "User-Agent": "TelegramAutoPostPro-CloudVault/2.0"
            },
            method="POST"
        )

        loop = asyncio.get_event_loop()
        def _send():
            with urllib.request.urlopen(req, timeout=15) as res:
                return res.read().decode("utf-8")

        resp_text = await loop.run_in_executor(None, _send)
        logger.info(f"Cloud Vault push successful: {resp_text[:120]}")
        _pending_push = False
        return True
    except Exception as e:
        logger.warning(f"Cloud Vault push failed (will retry): {e}")
        return False

def schedule_vault_push():
    """
    Schedules a debounced vault push so rapid edits don't flood Hostinger.
    """
    global _is_restoring
    if _is_restoring:
        return
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_debounced_push())
    except RuntimeError:
        # If called outside an active event loop
        pass

async def _debounced_push():
    global _pending_push
    if _pending_push or _is_restoring:
        return
    _pending_push = True
    await asyncio.sleep(2.5)
    async with _sync_lock:
        await push_vault_state()

async def pull_vault_state() -> bool:
    """
    Pulls the latest persistent state from Hostinger and restores
    all tenant users, web auth sessions, accounts, posts, and session files.
    """
    global _is_restoring
    _is_restoring = True
    try:
        url = f"{HOSTINGER_VAULT_URL}?action=vault_pull&secret={VAULT_SECRET}"
        req = urllib.request.Request(
            url,
            headers={
                "X-Vault-Secret": VAULT_SECRET,
                "User-Agent": "TelegramAutoPostPro-CloudVault/2.0"
            },
            method="GET"
        )

        loop = asyncio.get_event_loop()
        def _fetch():
            with urllib.request.urlopen(req, timeout=15) as res:
                return res.read().decode("utf-8")

        resp_text = await loop.run_in_executor(None, _fetch)
        if not resp_text:
            return False

        data = json.loads(resp_text)
        if data.get("status") == "empty" or not data.get("users"):
            logger.info("Hostinger vault is empty, pushing initial snapshot.")
            _is_restoring = False
            schedule_vault_push()
            return True

        ensure_directories()

        # 1. Restore Registered Users
        vault_users = data.get("users", [])
        if vault_users:
            current_users = load_users()
            merged_users_map = {u["id"]: u for u in current_users}
            for vu in vault_users:
                merged_users_map[vu["id"]] = vu
            save_users(list(merged_users_map.values()), sync_vault=False)

        # 2. Restore Web Portal Auth Sessions (prevents sudden user logout from portal)
        vault_auth_sessions = data.get("auth_sessions", {})
        if vault_auth_sessions:
            try:
                import app.auth_mgr as am
                current_sessions = am.load_auth_sessions()
                current_sessions.update(vault_auth_sessions)
                am.ACTIVE_TOKENS = current_sessions
                am.save_auth_sessions(sync_vault=False)
            except Exception as e:
                logger.warning(f"Could not restore auth sessions: {e}")

        # 3. Restore User Data (accounts, posts, groups, config)
        user_data = data.get("user_data", {})
        restored_accounts_count = 0

        for uid, udata in user_data.items():
            if "accounts" in udata and udata["accounts"]:
                save_accounts(udata["accounts"], user_id=uid, sync_vault=False)
                restored_accounts_count += len(udata["accounts"])
                _reconstruct_sessions_from_accounts(udata["accounts"])

            if "posts" in udata and udata["posts"]:
                save_posts(udata["posts"], user_id=uid, sync_vault=False)

            if "groups" in udata and udata["groups"]:
                save_groups(udata["groups"], user_id=uid, sync_vault=False)

            if "config" in udata and udata["config"]:
                save_config(udata["config"], user_id=uid, sync_vault=False)

        # 4. Restore Admin Data if needed
        admin_data = data.get("admin_data", {})
        if admin_data:
            if "accounts" in admin_data and admin_data["accounts"]:
                current_admin_accs = get_accounts("admin")
                if not current_admin_accs:
                    save_accounts(admin_data["accounts"], user_id="admin", sync_vault=False)
                _reconstruct_sessions_from_accounts(admin_data["accounts"])

        msg = f"Cloud Vault: Restored {len(vault_users)} users & {restored_accounts_count} accounts from Hostinger persistent vault."
        logger.info(msg)
        add_log("SUCCESS", msg, user_id="admin")
        return True

    except Exception as e:
        logger.warning(f"Could not pull vault state from Hostinger: {e}")
        return False
    finally:
        _is_restoring = False

def _reconstruct_sessions_from_accounts(accounts: list):
    """
    Ensures that for any account with a string_session, the SQLite
    .session file exists in SESSIONS_DIR so Telethon can connect instantly.
    """
    for acc in accounts:
        session_name = acc.get("session_name")
        string_sess = acc.get("string_session")
        if session_name and string_sess:
            write_sqlite_session_from_string(session_name, string_sess)

async def start_cloud_vault_service():
    """
    Initializes the cloud vault on backend startup:
    1. Pulls state from Hostinger to restore any users/accounts.
    2. Starts periodic keep-alive & auto-sync loop to prevent Render from sleeping.
    """
    logger.info("Initializing Cloud Vault Service...")
    await pull_vault_state()

    # Launch background keep-alive and backup loop
    asyncio.create_task(_cloud_vault_keepalive_loop())

async def _cloud_vault_keepalive_loop():
    """
    Pings Render & Hostinger every 5 minutes to prevent Render free instance
    from spinning down with inactivity, and auto-syncs the vault.
    """
    await asyncio.sleep(60) # Initial grace period
    while True:
        try:
            loop = asyncio.get_event_loop()
            
            # Ping Render self
            def _ping_render():
                try:
                    urllib.request.urlopen(f"{RENDER_APP_URL}/docs", timeout=10)
                except Exception:
                    pass
            await loop.run_in_executor(None, _ping_render)

            # Ping Hostinger gateway
            def _ping_hostinger():
                try:
                    urllib.request.urlopen(f"{HOSTINGER_VAULT_URL}?action=status", timeout=10)
                except Exception:
                    pass
            await loop.run_in_executor(None, _ping_hostinger)

            # Periodic backup push
            await push_vault_state()

        except Exception as e:
            logger.debug(f"Keep-alive ping cycle exception: {e}")

        # Sleep for 5 minutes (300 seconds) - well below Render's 15-minute idle limit!
        await asyncio.sleep(300)
