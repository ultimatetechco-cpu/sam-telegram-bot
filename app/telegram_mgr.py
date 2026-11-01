import asyncio
import os
import re
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    PasswordHashInvalidError,
    FloodWaitError,
    PhoneNumberInvalidError,
    PhoneNumberBannedError,
    FreshResetAuthorisationForbiddenError
)
from telethon.tl import types
from telethon.tl.types import auth
from telethon.tl.functions.auth import ResendCodeRequest
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest, CheckChatInviteRequest

from app.config import SESSIONS_DIR, MEDIA_DIR
from app.storage import (
    get_accounts,
    get_account_by_id,
    upsert_account,
    add_log,
    save_draft,
    get_draft,
    upsert_post,
    get_post_by_id,
    get_groups,
    save_groups
)
from app.auth_mgr import get_user_by_id
from app.emoji_parser import (
    unparse_message_and_entities_to_html,
    extract_custom_emojis,
    sanitize_group_target
)

_clients: Dict[str, TelegramClient] = {}
_pending_auth: Dict[str, Dict[str, Any]] = {}

# Emulate official Telegram Desktop client to ensure login codes are routed properly
CLIENT_PARAMS = {
    "device_model": "Desktop",
    "system_version": "Windows 11 x64",
    "app_version": "5.6.3 x64",
    "lang_code": "en",
    "system_lang_code": "en"
}

def clean_phone_number(phone: str) -> str:
    cleaned = re.sub(r"[^\d+]", "", phone.strip())
    if not cleaned.startswith("+"):
        cleaned = "+" + cleaned
    return cleaned

def export_string_session_from_client(client: TelegramClient) -> Optional[str]:
    """Safely exports the authenticated Telegram session as a portable string."""
    try:
        if not client or not client.session:
            return None
        if isinstance(client.session, StringSession):
            return client.session.save()
        str_sess = StringSession()
        if hasattr(client.session, 'dc_id') and client.session.dc_id:
            str_sess.set_dc(client.session.dc_id, client.session.server_address, client.session.port)
        if hasattr(client.session, 'auth_key') and client.session.auth_key:
            str_sess.auth_key = client.session.auth_key
            return str_sess.save()
        return None
    except Exception:
        return None

async def get_client_for_account(acc: Dict[str, Any]) -> Optional[TelegramClient]:
    acc_id = acc.get("id")
    if not acc_id:
        return None

    if acc_id in _clients:
        client = _clients[acc_id]
        if client.is_connected() and await client.is_user_authorized():
            return client

    session_name = acc.get("session_name") or f"{acc_id}"
    session_path = SESSIONS_DIR / session_name
    session_file = session_path.with_suffix(".session")

    # If SQLite session file does not exist, reconstruct it from string_session immediately
    if (not session_file.exists() or session_file.stat().st_size == 0) and acc.get("string_session"):
        try:
            from app.cloud_vault import write_sqlite_session_from_string
            write_sqlite_session_from_string(session_name, acc["string_session"])
        except Exception:
            pass

    try:
        client = TelegramClient(str(session_path), int(acc["api_id"]), str(acc["api_hash"]), **CLIENT_PARAMS)
        await client.connect()
        if not await client.is_user_authorized():
            # Try once more if string_session is present (e.g. stale or partial session file)
            if acc.get("string_session"):
                try:
                    from app.cloud_vault import write_sqlite_session_from_string
                    write_sqlite_session_from_string(session_name, acc["string_session"])
                    await client.connect()
                except Exception:
                    pass

            if not await client.is_user_authorized():
                add_log("WARNING", f"Account {acc.get('phone')} session is not authorized.", user_id=acc.get("owner_user_id"))
                return None

        # Auto-extract and persist string_session if not already saved
        if not acc.get("string_session"):
            str_sess = export_string_session_from_client(client)
            if str_sess:
                acc["string_session"] = str_sess
                upsert_account(acc, user_id=acc.get("owner_user_id"))

        _clients[acc_id] = client
        return client
    except Exception as e:
        add_log("ERROR", f"Failed to initialize client for {acc.get('phone')}: {e}", user_id=acc.get("owner_user_id"))
        return None

async def disconnect_all_clients():
    for acc_id, client in list(_clients.items()):
        try:
            if client.is_connected():
                await client.disconnect()
        except Exception:
            pass
    _clients.clear()

# ----------------- LOGIN & OTP FLOW -----------------

async def start_login(api_id: int, api_hash: str, phone: str, force_sms: bool = False, owner_user_id: Optional[str] = None) -> Dict[str, Any]:
    phone = clean_phone_number(phone)
    safe_phone_tag = phone.replace("+", "plus_")
    session_path = SESSIONS_DIR / f"temp_{safe_phone_tag}"

    # Check subscription & plan limits
    if owner_user_id and owner_user_id not in ["admin", "user_admin"]:
        user = get_user_by_id(owner_user_id)
        if not user:
            return {"status": "error", "message": "User not found."}
        if user.get("status") in ["expired", "suspended"]:
            return {"status": "error", "message": f"Your subscription is {user.get('status')}. Please contact admin to renew."}
        user_accounts = get_accounts(owner_user_id)
        max_accounts = user.get("max_accounts", 1)
        if len(user_accounts) >= max_accounts:
            return {"status": "error", "message": f"Account limit reached! Your plan allows a maximum of {max_accounts} account(s) ($10=1, $20=2, $30=3). Please upgrade your subscription tier."}

    # Clean up previous pending connection if any
    if phone in _pending_auth:
        try:
            old_client = _pending_auth[phone].get("client")
            if old_client and old_client.is_connected():
                await old_client.disconnect()
        except Exception:
            pass
        _pending_auth.pop(phone, None)

    # Clean previous temp session file if exists to ensure clean handshake
    temp_file = session_path.with_suffix(".session")
    if temp_file.exists():
        try:
            os.remove(temp_file)
        except Exception:
            pass

    add_log("INFO", f"Connecting to Telegram for {phone} (API ID: {api_id})...", user_id=owner_user_id)
    client = TelegramClient(str(session_path), api_id, api_hash, **CLIENT_PARAMS)
    await client.connect()

    try:
        sent_code = await client.send_code_request(phone, force_sms=force_sms)
        
        # Analyze delivery channel so the user knows exactly where to look
        delivery_channel = "Telegram App"
        delivery_details = "Check the official chat named 'Telegram' (with a blue checkmark, ID 777000) inside your active Telegram app."
        code_type_name = type(sent_code.type).__name__

        if isinstance(sent_code.type, auth.SentCodeTypeApp):
            delivery_channel = "Telegram App"
            delivery_details = "Code sent directly to your Telegram App! Open Telegram on your phone/PC and check the 'Telegram' service chat."
        elif isinstance(sent_code.type, auth.SentCodeTypeSms):
            delivery_channel = "SMS Text Message"
            delivery_details = "Telegram sent the code via SMS to your mobile phone number (NOT the Telegram app)! Please check your phone's SMS messages inbox."
        elif isinstance(sent_code.type, auth.SentCodeTypeFragmentSms):
            delivery_channel = "Fragment SMS"
            delivery_details = "Code sent via Fragment SMS."
        elif isinstance(sent_code.type, (auth.SentCodeTypeCall, auth.SentCodeTypeFlashCall, auth.SentCodeTypeMissedCall)):
            delivery_channel = "Phone Call"
            delivery_details = "Telegram is calling your phone number with the verification code."
        elif isinstance(sent_code.type, auth.SentCodeTypeEmailCode):
            delivery_channel = "Email"
            delivery_details = "Telegram sent the verification code to your registered email address."

        _pending_auth[phone] = {
            "client": client,
            "api_id": api_id,
            "api_hash": api_hash,
            "phone_code_hash": sent_code.phone_code_hash,
            "session_path": session_path,
            "delivery_channel": delivery_channel,
            "owner_user_id": owner_user_id
        }

        timeout = getattr(sent_code, "timeout", 60)
        add_log("SUCCESS", f"Verification code sent via [{delivery_channel}] to {phone}! {delivery_details}", user_id=owner_user_id)

        return {
            "status": "code_sent",
            "phone": phone,
            "phone_code_hash": sent_code.phone_code_hash,
            "delivery_channel": delivery_channel,
            "delivery_details": delivery_details,
            "timeout": timeout,
            "code_type": code_type_name
        }

    except PhoneNumberInvalidError:
        add_log("ERROR", f"The phone number '{phone}' is invalid. Please include international country code (e.g. +1..., +44...).", user_id=owner_user_id)
        await client.disconnect()
        return {"status": "error", "message": "Phone number is invalid. Make sure country code is included (e.g. +1...)."}
    except PhoneNumberBannedError:
        add_log("ERROR", f"This phone number '{phone}' has been banned by Telegram.", user_id=owner_user_id)
        await client.disconnect()
        return {"status": "error", "message": "This phone number is banned by Telegram."}
    except FloodWaitError as e:
        add_log("ERROR", f"Telegram FloodWaitError: Telegram requests waiting for {e.seconds} seconds.", user_id=owner_user_id)
        await client.disconnect()
        return {"status": "error", "message": f"Telegram FloodWait: Please wait {e.seconds} seconds before requesting a code again."}
    except Exception as e:
        add_log("ERROR", f"Failed to send code to {phone}: {e}", user_id=owner_user_id)
        await client.disconnect()
        return {"status": "error", "message": str(e)}

async def resend_code_sms(phone: str, owner_user_id: Optional[str] = None) -> Dict[str, Any]:
    phone = clean_phone_number(phone)
    pending = _pending_auth.get(phone)
    if not pending:
        return {"status": "error", "message": "No active login session for this phone. Please request code first."}

    client: TelegramClient = pending["client"]
    phone_code_hash = pending["phone_code_hash"]
    uid = pending.get("owner_user_id") or owner_user_id

    try:
        add_log("INFO", f"Requesting code resend via SMS for {phone}...", user_id=uid)
        sent_code = await client(ResendCodeRequest(phone=phone, phone_code_hash=phone_code_hash))
        pending["phone_code_hash"] = sent_code.phone_code_hash
        add_log("SUCCESS", f"Code resent via SMS to {phone}! Please check your phone's SMS messages inbox.", user_id=uid)
        return {
            "status": "code_sent",
            "phone": phone,
            "delivery_channel": "SMS Text Message",
            "delivery_details": "Code has been resent via SMS! Check your phone's SMS text inbox."
        }
    except FloodWaitError as e:
        return {"status": "error", "message": f"Telegram FloodWait: wait {e.seconds} seconds before resending."}
    except Exception as e:
        return {"status": "error", "message": f"Failed to resend SMS: {e}"}

async def submit_login_code(phone: str, code: str, password: Optional[str] = None, owner_user_id: Optional[str] = None) -> Dict[str, Any]:
    phone = clean_phone_number(phone)
    code = re.sub(r"[^\d]", "", (code or "").strip())
    pending = _pending_auth.get(phone)
    if not pending:
        return {"status": "error", "message": "No login session in progress for this phone. Please request code first."}

    client: TelegramClient = pending["client"]
    phone_code_hash = pending["phone_code_hash"]
    effective_user_id = pending.get("owner_user_id") or owner_user_id

    # Enforce limit right before final save
    if effective_user_id and effective_user_id not in ["admin", "user_admin"]:
        user = get_user_by_id(effective_user_id)
        if user:
            cur_accs = get_accounts(effective_user_id)
            if len(cur_accs) >= user.get("max_accounts", 1):
                return {"status": "error", "message": f"Account limit reached ({user.get('max_accounts', 1)} max). Upgrade your plan to add more."}

    try:
        if pending.get("needs_2fa"):
            if not password:
                return {
                    "status": "2fa_required",
                    "message": "Two-step verification (2FA) is enabled on this account. Please enter your 2FA password."
                }
            await client.sign_in(password=password)
        else:
            try:
                await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
            except SessionPasswordNeededError:
                pending["needs_2fa"] = True
                if not password:
                    return {
                        "status": "2fa_required",
                        "message": "Two-step verification (2FA) is enabled on this account. Please enter your 2FA password."
                    }
                await client.sign_in(password=password)

        me = await client.get_me()
        tg_user_id = str(me.id)
        if effective_user_id and effective_user_id not in ["admin", "user_admin"]:
            acc_id = f"acc_{effective_user_id}_{tg_user_id}"
            final_session_name = f"session_{effective_user_id}_{tg_user_id}"
        else:
            acc_id = f"acc_{tg_user_id}"
            final_session_name = f"session_{tg_user_id}"

        is_premium = getattr(me, "premium", False)
        final_session_path = SESSIONS_DIR / final_session_name

        # Disconnect temp, rename session file to permanent with lock retry, reconnect
        await client.disconnect()
        await asyncio.sleep(0.5)

        temp_session = pending["session_path"].with_suffix(".session")
        perm_session = final_session_path.with_suffix(".session")
        if temp_session.exists():
            if perm_session.exists():
                try:
                    os.remove(perm_session)
                except Exception:
                    pass
            for _ in range(5):
                try:
                    os.rename(temp_session, perm_session)
                    break
                except PermissionError:
                    await asyncio.sleep(0.5)

        # Re-initialize permanent client with official parameters
        perm_client = TelegramClient(str(final_session_path), pending["api_id"], pending["api_hash"], **CLIENT_PARAMS)
        await perm_client.connect()
        _clients[acc_id] = perm_client

        # Export string session for cloud persistence across restarts
        str_sess = export_string_session_from_client(perm_client)

        account_info = {
            "id": acc_id,
            "user_id": tg_user_id,
            "owner_user_id": effective_user_id or "admin",
            "first_name": me.first_name or "",
            "last_name": me.last_name or "",
            "username": me.username or "",
            "phone": phone,
            "api_id": pending["api_id"],
            "api_hash": pending["api_hash"],
            "session_name": final_session_name,
            "string_session": str_sess,
            "is_premium": is_premium,
            "is_active": True,
            "status": "Ready",
            "last_used": None,
            "flood_wait_until": None
        }

        upsert_account(account_info, user_id=effective_user_id)
        _pending_auth.pop(phone, None)

        premium_tag = " [Telegram Premium ⭐ 3D Emojis Supported]" if is_premium else ""
        add_log("SUCCESS", f"Account connected: {me.first_name} (@{me.username or phone}){premium_tag}", user_id=effective_user_id)
        return {"status": "success", "account": account_info}

    except PhoneCodeInvalidError:
        return {"status": "error", "message": "The verification code entered is incorrect."}
    except PhoneCodeExpiredError:
        return {"status": "error", "message": "The verification code has expired. Please request a new one."}
    except PasswordHashInvalidError:
        return {"status": "2fa_required", "message": "Incorrect 2FA password! Please re-check your password and try again."}
    except FloodWaitError as e:
        minutes = int(e.seconds // 60)
        seconds = int(e.seconds % 60)
        time_str = f"{minutes} min {seconds} sec" if minutes > 0 else f"{seconds} seconds"
        add_log("WARNING", f"Telegram FloodWait on sign-in: {time_str} wait required.", user_id=effective_user_id)
        return {
            "status": "error",
            "message": f"Telegram Security Cooldown (FloodWait): Telegram requires waiting {time_str} before attempting 2FA again on this number. You can wait or connect instantly using 'Method 2: String Session' without waiting."
        }
    except Exception as e:
        add_log("ERROR", f"Sign-in error: {e}", user_id=effective_user_id)
        return {"status": "error", "message": str(e)}

# ----------------- ALTERNATIVE: LOGIN VIA STRING SESSION -----------------

async def login_with_string_session(api_id: int, api_hash: str, session_str: str, owner_user_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Allows instant login using a Telethon StringSession without needing OTP delivery.
    """
    session_str = session_str.strip()
    if not session_str:
        return {"status": "error", "message": "Session string is empty."}

    if owner_user_id and owner_user_id not in ["admin", "user_admin"]:
        user = get_user_by_id(owner_user_id)
        if not user:
            return {"status": "error", "message": "User not found."}
        if user.get("status") in ["expired", "suspended"]:
            return {"status": "error", "message": f"Your subscription is {user.get('status')}. Please contact admin to renew."}
        user_accounts = get_accounts(owner_user_id)
        max_accounts = user.get("max_accounts", 1)
        if len(user_accounts) >= max_accounts:
            return {"status": "error", "message": f"Account limit reached! Your plan allows a maximum of {max_accounts} account(s) ($10=1, $20=2, $30=3). Please upgrade your subscription tier."}

    add_log("INFO", "Validating Telegram String Session...", user_id=owner_user_id)
    try:
        client = TelegramClient(StringSession(session_str), api_id, api_hash, **CLIENT_PARAMS)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return {"status": "error", "message": "String session is expired or not authorized."}

        me = await client.get_me()
        tg_user_id = str(me.id)
        if owner_user_id and owner_user_id not in ["admin", "user_admin"]:
            acc_id = f"acc_{owner_user_id}_{tg_user_id}"
            final_session_name = f"session_{owner_user_id}_{tg_user_id}"
        else:
            acc_id = f"acc_{tg_user_id}"
            final_session_name = f"session_{tg_user_id}"

        is_premium = getattr(me, "premium", False)
        phone = me.phone or f"+user_{tg_user_id}"
        if not phone.startswith("+"):
            phone = "+" + phone

        # Convert to permanent sqlite session file with proper DC and auth key
        final_session_path = SESSIONS_DIR / final_session_name
        file_client = TelegramClient(str(final_session_path), api_id, api_hash, **CLIENT_PARAMS)
        await file_client.connect()
        if hasattr(client.session, 'dc_id') and client.session.dc_id:
            file_client.session.set_dc(client.session.dc_id, client.session.server_address, client.session.port)
        file_client.session.auth_key = client.session.auth_key
        file_client.session.save()
        await client.disconnect()

        await file_client.connect()
        _clients[acc_id] = file_client

        account_info = {
            "id": acc_id,
            "user_id": tg_user_id,
            "owner_user_id": owner_user_id or "admin",
            "first_name": me.first_name or "",
            "last_name": me.last_name or "",
            "username": me.username or "",
            "phone": phone,
            "api_id": api_id,
            "api_hash": api_hash,
            "session_name": final_session_name,
            "string_session": session_str,
            "is_premium": is_premium,
            "is_active": True,
            "status": "Ready",
            "last_used": None,
            "flood_wait_until": None
        }

        upsert_account(account_info, user_id=owner_user_id)
        premium_tag = " [Telegram Premium ⭐ 3D Emojis Supported]" if is_premium else ""
        add_log("SUCCESS", f"String Session connected: {me.first_name} (@{me.username or phone}){premium_tag}", user_id=owner_user_id)
        return {"status": "success", "account": account_info}

    except Exception as e:
        add_log("ERROR", f"String session login failed: {e}", user_id=owner_user_id)
        return {"status": "error", "message": str(e)}

# ----------------- SOURCE POST FETCHER -----------------

async def fetch_telegram_post(chat_input: str, message_id: Optional[int] = None, target_post_id: Optional[str] = None, user_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Fetches message directly from Telegram (Saved Messages, Channel, or Group),
    extracting 3D/animated custom emojis, styling, and media attachments intact.
    Can save into a specific post in the post pool.
    """
    accounts = get_accounts(user_id=user_id)
    active_accounts = [a for a in accounts if a.get("is_active", True)]
    if not active_accounts:
        return {"status": "error", "message": "No active Telegram account available. Please add/connect an account first."}

    client = None
    for acc in active_accounts:
        c = await get_client_for_account(acc)
        if c:
            client = c
            break

    if not client:
        return {"status": "error", "message": "Could not connect to any active Telegram account."}

    add_log("INFO", f"Fetching post from Telegram chat: '{chat_input}'...", user_id=user_id)

    try:
        # Determine target entity
        target_entity = chat_input.strip()
        if target_entity.lower() in ["me", "saved", "saved messages", "saved_messages"]:
            entity = "me"
        elif "t.me/" in target_entity:
            parts = target_entity.rstrip("/").split("/")
            if parts[-1].isdigit():
                message_id = int(parts[-1])
                target_entity = parts[-2]
            entity = target_entity
        else:
            entity = sanitize_group_target(target_entity)

        # Retrieve message
        if message_id:
            msg = await client.get_messages(entity, ids=message_id)
        else:
            messages = await client.get_messages(entity, limit=1)
            msg = messages[0] if messages else None

        if not msg:
            return {"status": "error", "message": "Message not found or chat is empty."}

        # Extract text & entities with custom 3D emojis
        raw_text = msg.text or ""
        html_content = unparse_message_and_entities_to_html(raw_text, msg.entities)
        custom_emojis = extract_custom_emojis(msg.entities)

        media_path = None
        media_name = None
        media_filename = None
        media_url = None
        has_media = bool(msg.media)

        if has_media:
            add_log("INFO", "Downloading media attachment from source post...", user_id=user_id)
            downloaded = await client.download_media(msg, file=str(MEDIA_DIR))
            if downloaded:
                media_path = downloaded
                media_name = os.path.basename(downloaded)
                media_filename = media_name
                media_url = f"/media/{media_name}"

        post_data = {
            "html_content": html_content,
            "media_path": media_path,
            "media_name": media_name,
            "media_filename": media_filename,
            "media_url": media_url,
            "has_media": has_media,
            "source_chat": chat_input,
            "source_msg_id": msg.id,
            "custom_emojis_count": len(custom_emojis),
            "custom_emojis": custom_emojis
        }

        if target_post_id:
            post_data["id"] = target_post_id
            existing = get_post_by_id(target_post_id, user_id=user_id)
            if existing:
                post_data["title"] = existing.get("title", f"Post {target_post_id}")
            upsert_post(post_data, user_id=user_id)
        else:
            save_draft(post_data, user_id=user_id)

        add_log("SUCCESS", f"Post fetched successfully! (Chars: {len(raw_text)}, 3D Emojis: {len(custom_emojis)}, Media: {has_media})", user_id=user_id)
        return {"status": "success", "post": post_data, "draft": post_data}

    except Exception as e:
        add_log("ERROR", f"Failed to fetch post: {e}", user_id=user_id)
        return {"status": "error", "message": str(e)}

# ----------------- ACCOUNT GROUPS SCANNER -----------------

async def fetch_account_groups(account_id: str, user_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Scans and returns all groups and supergroups joined by the selected Telegram account.
    """
    acc = get_account_by_id(account_id, user_id=user_id)
    if not acc:
        return {"status": "error", "message": f"Account with ID '{account_id}' was not found."}

    client = await get_client_for_account(acc)
    if not client:
        return {"status": "error", "message": f"Could not connect client for {acc.get('phone')}."}

    acc_label = f"{acc.get('first_name', '')} ({acc.get('phone')})"
    add_log("INFO", f"Scanning all joined groups for account {acc_label}...", user_id=user_id)

    found_groups = []
    try:
        async for dialog in client.iter_dialogs():
            if dialog.is_group:
                entity = dialog.entity
                username = getattr(entity, "username", None)
                target = f"@{username}" if username else str(dialog.id)
                found_groups.append({
                    "id": dialog.id,
                    "title": dialog.name,
                    "username": username,
                    "target": target,
                    "participants_count": getattr(entity, "participants_count", None),
                    "is_megagroup": getattr(entity, "megagroup", False)
                })

        add_log("SUCCESS", f"Found {len(found_groups)} joined groups on account {acc_label}!", user_id=user_id)
        return {"status": "success", "groups": found_groups, "count": len(found_groups)}
    except Exception as e:
        add_log("ERROR", f"Error scanning groups for {acc_label}: {e}", user_id=user_id)
        return {"status": "error", "message": str(e)}

async def import_account_groups(account_id: str, targets: Optional[List[str]] = None, user_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Imports scanned groups directly into the target groups list.
    """
    scan_res = await fetch_account_groups(account_id, user_id=user_id)
    if scan_res.get("status") != "success":
        return scan_res

    scanned = scan_res.get("groups", [])
    if targets:
        target_set = set(targets)
        scanned = [g for g in scanned if g["target"] in target_set or str(g["id"]) in target_set]

    existing_groups = {g["target"]: g for g in get_groups(user_id=user_id)}
    new_added = 0

    for g in scanned:
        tg = g["target"]
        if tg not in existing_groups:
            existing_groups[tg] = {
                "target": tg,
                "title": g.get("title", tg),
                "status": "Ready",
                "last_posted": None,
                "last_result": None,
                "fail_count": 0
            }
            new_added += 1

    save_groups(list(existing_groups.values()), user_id=user_id)
    add_log("SUCCESS", f"Imported {new_added} groups from account into target list. Total active groups: {len(existing_groups)}", user_id=user_id)
    return {
        "status": "success",
        "added": new_added,
        "total": len(existing_groups)
    }
