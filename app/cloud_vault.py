import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import urllib.request
import urllib.error
import zlib
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List

from telethon.sessions import StringSession, SQLiteSession

from app.config import DATA_DIR, MEDIA_DIR, SESSIONS_DIR, ensure_directories
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

HOSTINGER_VAULT_URL = os.environ.get("HOSTINGER_VAULT_URL", "https://omeglevideochat.online/telegrambot/api_proxy.php")
VAULT_SECRET = os.environ.get("VAULT_SECRET", "sam_telegram_autopost_live_sync_2026_x99")
RENDER_APP_URL = os.environ.get("RENDER_APP_URL", "https://sam-telegram-bot-hgm1.onrender.com")

_ENC_KEY = hashlib.sha256((VAULT_SECRET + "_enc_v1").encode("utf-8")).digest()
_MAC_KEY = hashlib.sha256((VAULT_SECRET + "_mac_v1").encode("utf-8")).digest()

_sync_lock = asyncio.Lock()
_pending_push: bool = False
_is_restoring: bool = False
_vault_pull_succeeded: bool = False
_vault_status: Dict[str, Any] = {
    "connected": False,
    "last_sync": None,
    "last_pull": None,
    "error": None,
    "needs_hostinger_upload": False
}

def get_vault_status() -> Dict[str, Any]:
    return dict(_vault_status)

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

def _collect_media_files_for_posts(posts_lists: List[List[Dict[str, Any]]], max_file_bytes: int = 3_000_000, max_total_bytes: int = 12_000_000) -> Dict[str, str]:
    """Encodes referenced post media files from MEDIA_DIR as base64 strings within size caps."""
    media_map: Dict[str, str] = {}
    total_bytes = 0
    seen = set()
    for posts in posts_lists:
        for p in posts:
            fname = p.get("media_filename")
            if not fname and p.get("media_path"):
                fname = os.path.basename(str(p["media_path"]).replace("\\", "/"))
            if not fname or fname in seen:
                continue
            seen.add(fname)
            fpath = MEDIA_DIR / os.path.basename(fname)
            if not fpath.is_file():
                continue
            try:
                sz = fpath.stat().st_size
                if sz <= 0 or sz > max_file_bytes or (total_bytes + sz) > max_total_bytes:
                    continue
                raw = fpath.read_bytes()
                media_map[os.path.basename(fname)] = base64.b64encode(raw).decode("ascii")
                total_bytes += sz
            except Exception:
                pass
    return media_map

def _restore_media_files(media_map: Dict[str, str], posts_lists: List[List[Dict[str, Any]]]) -> None:
    """Writes restored base64 media files to MEDIA_DIR and normalizes post media_path."""
    if media_map:
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        for fname, b64_str in media_map.items():
            clean_name = os.path.basename(str(fname))
            if not clean_name:
                continue
            target = MEDIA_DIR / clean_name
            if not target.exists():
                try:
                    target.write_bytes(base64.b64decode(b64_str))
                except Exception:
                    pass

    for posts in posts_lists:
        for p in posts:
            fname = p.get("media_filename")
            if not fname and p.get("media_path"):
                fname = os.path.basename(str(p["media_path"]).replace("\\", "/"))
            if fname:
                clean_name = os.path.basename(fname)
                local_path = MEDIA_DIR / clean_name
                if local_path.is_file():
                    p["media_path"] = str(local_path)
                    p["media_filename"] = clean_name
                    p["media_url"] = f"/media/{clean_name}"

def _has_real_tenant_data(users: List[Dict[str, Any]], user_data: Dict[str, Any]) -> bool:
    for udata in user_data.values():
        if udata.get("accounts") or udata.get("groups"):
            return True
    if len(users) > 1:
        return True
    return False

def build_vault_payload() -> Dict[str, Any]:
    """
    Constructs a comprehensive snapshot of all tenant users, web auth tokens,
    accounts (with string sessions), posts, groups, media, and configurations.
    """
    ensure_directories()
    users = load_users()
    auth_sessions = load_auth_sessions()
    user_data = {}
    all_posts_lists = []

    for u in users:
        uid = u["id"]
        if uid in ["admin", "user_admin"]:
            continue
        user_accounts = _enrich_accounts_with_string_sessions(get_accounts(uid), user_id=uid)
        u_posts = get_posts(uid)
        all_posts_lists.append(u_posts)
        user_data[uid] = {
            "accounts": user_accounts,
            "posts": u_posts,
            "groups": get_groups(uid),
            "config": load_config(uid)
        }

    admin_accounts = _enrich_accounts_with_string_sessions(get_accounts("admin"), user_id="admin")
    admin_posts = get_posts("admin")
    all_posts_lists.append(admin_posts)
    admin_data = {
        "accounts": admin_accounts,
        "posts": admin_posts,
        "groups": get_groups("admin"),
        "config": load_config("admin")
    }

    email_cfg = {}
    try:
        from app.email_service import load_email_config
        email_cfg = load_email_config()
    except Exception:
        pass

    return {
        "version": "2.1",
        "synced_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "users": users,
        "auth_sessions": auth_sessions,
        "user_data": user_data,
        "admin_data": admin_data,
        "email_config": email_cfg,
        "media_files": _collect_media_files_for_posts(all_posts_lists)
    }

async def push_vault_state(force: bool = False) -> bool:
    """
    Uploads the current tenant state to Hostinger's persistent storage.
    Refuses to overwrite remote vault if startup pull hasn't succeeded yet (unless forced after verified local mutations).
    """
    global _is_restoring, _vault_pull_succeeded
    if _is_restoring:
        return False

    # Safety guard: if we failed to pull on startup, retry pull BEFORE ever pushing empty startup state
    if not _vault_pull_succeeded and not force:
        pulled = await pull_vault_state()
        if not pulled:
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
                "User-Agent": "TelegramAutoPostPro-CloudVault/2.1"
            },
            method="POST"
        )

        loop = asyncio.get_running_loop()
        def _send():
            with urllib.request.urlopen(req, timeout=20) as res:
                return res.read().decode("utf-8")

        resp_text = await loop.run_in_executor(None, _send)
        resp_obj = json.loads(resp_text)
        if resp_obj.get("status") == "success":
            _vault_status["connected"] = True
            _vault_status["needs_hostinger_upload"] = False
            _vault_status["last_sync"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _vault_status["error"] = None
            logger.info(f"Cloud Vault push successful: {resp_text[:120]}")
            return True

        _vault_status["connected"] = False
        _vault_status["error"] = resp_obj.get("message", "Unexpected response from vault_push")
        return False
    except urllib.error.HTTPError as e:
        if e.code == 404:
            _vault_status["connected"] = False
            _vault_status["needs_hostinger_upload"] = True
            _vault_status["error"] = "Hostinger api_proxy.php returned 404 (upload hostinger_live_ready.zip to Hostinger)"
        else:
            _vault_status["connected"] = False
            _vault_status["error"] = f"HTTP {e.code} during vault push"
        logger.warning(f"Cloud Vault push failed: {_vault_status['error']}")
        return False
    except Exception as e:
        _vault_status["connected"] = False
        _vault_status["error"] = str(e)
        logger.warning(f"Cloud Vault push failed (will retry): {e}")
        return False

def schedule_vault_push():
    """
    Schedules a debounced vault push so rapid edits coalesce cleanly without deadlocking.
    """
    global _pending_push, _is_restoring
    if _is_restoring or _pending_push:
        return
    try:
        loop = asyncio.get_running_loop()
        _pending_push = True
        loop.create_task(_debounced_push())
    except RuntimeError:
        pass

async def _debounced_push():
    global _pending_push
    try:
        await asyncio.sleep(2.0)
    finally:
        _pending_push = False
    async with _sync_lock:
        # Force push is safe here because this was triggered by an explicit local save/edit
        await push_vault_state(force=True)

async def pull_vault_state() -> bool:
    """
    Pulls the latest persistent state from Hostinger and restores
    all tenant users, web auth sessions, accounts, posts, media, and session files.
    """
    global _is_restoring, _vault_pull_succeeded
    _is_restoring = True
    try:
        url = f"{HOSTINGER_VAULT_URL}?action=vault_pull&secret={VAULT_SECRET}"
        req = urllib.request.Request(
            url,
            headers={
                "X-Vault-Secret": VAULT_SECRET,
                "User-Agent": "TelegramAutoPostPro-CloudVault/2.1"
            },
            method="GET"
        )

        loop = asyncio.get_running_loop()
        def _fetch():
            with urllib.request.urlopen(req, timeout=12) as res:
                return res.read().decode("utf-8")

        resp_text = await loop.run_in_executor(None, _fetch)
        if not resp_text:
            return False

        data = json.loads(resp_text)
        _vault_pull_succeeded = True
        _vault_status["connected"] = True
        _vault_status["needs_hostinger_upload"] = False
        _vault_status["last_pull"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _vault_status["error"] = None

        if data.get("status") == "empty" or not data.get("users"):
            logger.info("Hostinger vault is empty, pushing initial snapshot.")
            _is_restoring = False
            await push_vault_state(force=True)
            return True

        ensure_directories()

        # 0. Restore uploaded media files before normalizing post paths
        media_files = data.get("media_files", {})
        all_posts_lists = []
        user_data = data.get("user_data", {})
        for udata in user_data.values():
            if "posts" in udata and isinstance(udata["posts"], list):
                all_posts_lists.append(udata["posts"])
        admin_data = data.get("admin_data", {})
        if isinstance(admin_data.get("posts"), list):
            all_posts_lists.append(admin_data["posts"])
        _restore_media_files(media_files, all_posts_lists)

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
        restored_accounts_count = 0
        for uid, udata in user_data.items():
            if "accounts" in udata and udata["accounts"] is not None:
                save_accounts(udata["accounts"], user_id=uid, sync_vault=False)
                restored_accounts_count += len(udata["accounts"])
                _reconstruct_sessions_from_accounts(udata["accounts"])

            if "posts" in udata and udata["posts"]:
                save_posts(udata["posts"], user_id=uid, sync_vault=False)

            if "groups" in udata and udata["groups"] is not None:
                save_groups(udata["groups"], user_id=uid, sync_vault=False)

            if "config" in udata and udata["config"]:
                save_config(udata["config"], user_id=uid, sync_vault=False)

        # 4. Restore Admin Data if needed
        if admin_data:
            if "accounts" in admin_data and admin_data["accounts"]:
                current_admin_accs = get_accounts("admin")
                if not current_admin_accs:
                    save_accounts(admin_data["accounts"], user_id="admin", sync_vault=False)
                _reconstruct_sessions_from_accounts(admin_data["accounts"])

        # 5. Restore Email Config if present and local has no password
        vault_email = data.get("email_config")
        if vault_email and vault_email.get("smtp_password"):
            try:
                from app.email_service import load_email_config, save_email_config
                cur_email = load_email_config()
                if not cur_email.get("smtp_password"):
                    save_email_config(vault_email)
            except Exception:
                pass

        msg = f"Cloud Vault: Restored {len(vault_users)} users & {restored_accounts_count} accounts from Hostinger persistent vault."
        logger.info(msg)
        add_log("SUCCESS", msg, user_id="admin")
        return True

    except urllib.error.HTTPError as e:
        if e.code == 404:
            _vault_status["connected"] = False
            _vault_status["needs_hostinger_upload"] = True
            _vault_status["error"] = "Hostinger api_proxy.php needs update (vault_pull returned 404). Please extract hostinger_live_ready.zip on Hostinger."
            add_log("WARNING", _vault_status["error"], user_id="admin")
        else:
            _vault_status["connected"] = False
            _vault_status["error"] = f"HTTP {e.code} pulling vault"
        return False
    except Exception as e:
        _vault_status["connected"] = False
        _vault_status["error"] = str(e)
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

# ----------------- ENCRYPTED BROWSER AUTO-VAULT FAIL-SAFE -----------------

def _xor_keystream(nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hashlib.sha256(_ENC_KEY + nonce + counter.to_bytes(4, "big")).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])

def encrypt_user_snapshot(data: Dict[str, Any]) -> str:
    """Encrypts and HMAC-authenticates a tenant snapshot using stdlib crypto."""
    raw = zlib.compress(json.dumps(data, ensure_ascii=False).encode("utf-8"), level=6)
    nonce = secrets.token_bytes(16)
    ks = _xor_keystream(nonce, len(raw))
    ciphertext = bytes(a ^ b for a, b in zip(raw, ks))
    mac = hmac.new(_MAC_KEY, nonce + ciphertext, hashlib.sha256).digest()
    return "EV1." + base64.urlsafe_b64encode(nonce + mac + ciphertext).decode("ascii")

def decrypt_user_snapshot(blob: str) -> Optional[Dict[str, Any]]:
    """Verifies HMAC signature and decrypts a tenant snapshot blob."""
    try:
        if not blob or not isinstance(blob, str) or not blob.startswith("EV1."):
            return None
        raw_bytes = base64.urlsafe_b64decode(blob[4:].encode("ascii"))
        if len(raw_bytes) < 48:
            return None
        nonce = raw_bytes[:16]
        mac = raw_bytes[16:48]
        ciphertext = raw_bytes[48:]
        expected_mac = hmac.new(_MAC_KEY, nonce + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(mac, expected_mac):
            return None
        ks = _xor_keystream(nonce, len(ciphertext))
        plain = bytes(a ^ b for a, b in zip(ciphertext, ks))
        return json.loads(zlib.decompress(plain).decode("utf-8"))
    except Exception:
        return None

def export_tenant_snapshot(user: Dict[str, Any]) -> Dict[str, Any]:
    """Exports an encrypted per-user backup snapshot for client-side fail-safe storage."""
    uid = user["id"]
    accounts = _enrich_accounts_with_string_sessions(get_accounts(uid), user_id=uid)
    groups = get_groups(uid)
    posts = get_posts(uid)
    config = load_config(uid)
    media_files = _collect_media_files_for_posts([posts], max_file_bytes=750_000, max_total_bytes=1_800_000)

    payload = {
        "v": 1,
        "user_id": uid,
        "email": (user.get("email") or "").lower(),
        "username": (user.get("username") or "").lower(),
        "accounts": accounts,
        "groups": groups,
        "posts": posts,
        "config": config,
        "media_files": media_files,
        "saved_at": int(time.time())
    }
    blob = encrypt_user_snapshot(payload)
    has_custom_posts = len(posts) != 3 or any(p.get("updated_at") or p.get("has_media") for p in posts)
    return {
        "snapshot": blob,
        "accounts_count": len(accounts),
        "groups_count": len(groups),
        "posts_count": len(posts),
        "has_data": len(accounts) > 0 or len(groups) > 0 or has_custom_posts
    }

def restore_tenant_snapshot(blob: str, current_user: Dict[str, Any]) -> Dict[str, Any]:
    """Restores a tenant's encrypted snapshot if authenticated user matches owner."""
    snap = decrypt_user_snapshot(blob)
    if not snap:
        return {"status": "error", "message": "Invalid or corrupted backup snapshot."}

    uid = current_user["id"]
    cur_email = (current_user.get("email") or "").lower()
    cur_user = (current_user.get("username") or "").lower()
    owner_ok = (
        snap.get("user_id") == uid
        or (cur_email and snap.get("email") == cur_email)
        or (cur_user and snap.get("username") == cur_user)
    )
    if not owner_ok:
        return {"status": "error", "message": "Backup snapshot belongs to a different account."}

    accounts = snap.get("accounts") or []
    groups = snap.get("groups") or []
    posts = snap.get("posts") or []
    config = snap.get("config") or {}
    media_files = snap.get("media_files") or {}

    if media_files or posts:
        _restore_media_files(media_files, [posts] if posts else [])

    restored_accs = 0
    if accounts:
        for acc in accounts:
            acc["owner_user_id"] = uid
        save_accounts(accounts, user_id=uid, sync_vault=True)
        _reconstruct_sessions_from_accounts(accounts)
        restored_accs = len(accounts)

    if groups:
        save_groups(groups, user_id=uid, sync_vault=True)

    if posts:
        save_posts(posts, user_id=uid, sync_vault=True)

    if config:
        save_config(config, user_id=uid, sync_vault=True)

    add_log(
        "SUCCESS",
        f"Auto-restored tenant backup: {restored_accs} account(s), {len(groups)} group(s), {len(posts)} post(s).",
        user_id=uid
    )
    return {
        "status": "restored",
        "accounts_count": restored_accs,
        "groups_count": len(groups),
        "posts_count": len(posts)
    }

# ----------------- LIFECYCLE & KEEP-ALIVE -----------------

async def start_cloud_vault_service():
    """
    Initializes the cloud vault on backend startup:
    1. Awaits vault pull from Hostinger so users/accounts/sessions exist before first request.
    2. Launches periodic keep-alive & auto-sync loop.
    """
    logger.info("Initializing Cloud Vault Service...")
    try:
        await asyncio.wait_for(pull_vault_state(), timeout=12.0)
    except Exception as e:
        logger.warning(f"Startup vault pull timed out or failed: {e}")

    # Launch background keep-alive and backup loop
    asyncio.create_task(_cloud_vault_keepalive_loop())

async def _cloud_vault_keepalive_loop():
    """
    Pings Render & Hostinger every 4 minutes to keep Render warm and syncs vault state.
    """
    await asyncio.sleep(60)
    while True:
        try:
            loop = asyncio.get_running_loop()

            # Ping Render self health endpoint
            def _ping_render():
                try:
                    req = urllib.request.Request(
                        f"{RENDER_APP_URL}/api/health",
                        headers={"User-Agent": "Mozilla/5.0 (compatible; TelegramAutoPostPro/2.1)"}
                    )
                    urllib.request.urlopen(req, timeout=10).read()
                except Exception:
                    pass
            await loop.run_in_executor(None, _ping_render)

            # Ping Hostinger gateway
            def _ping_hostinger():
                try:
                    req = urllib.request.Request(
                        f"{HOSTINGER_VAULT_URL}?action=status",
                        headers={"User-Agent": "TelegramAutoPostPro-CloudVault/2.1"}
                    )
                    urllib.request.urlopen(req, timeout=10).read()
                except Exception:
                    pass
            await loop.run_in_executor(None, _ping_hostinger)

            # Retry pull if still uninitialized, otherwise push latest state
            if not _vault_pull_succeeded:
                await pull_vault_state()
            else:
                await push_vault_state()

        except Exception as e:
            logger.debug(f"Keep-alive ping cycle exception: {e}")

        await asyncio.sleep(240)
