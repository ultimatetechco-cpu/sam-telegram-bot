import asyncio
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from app.config import (
    DATA_DIR,
    SESSIONS_DIR,
    MEDIA_DIR,
    ensure_directories
)
from app.emoji_parser import sanitize_group_target

# In-memory log buffer per user & global
GLOBAL_LOG_HISTORY: List[Dict[str, Any]] = []
USER_LOG_HISTORIES: Dict[str, List[Dict[str, Any]]] = {}
LOG_LISTENERS: List[Dict[str, Any]] = [] # list of {"queue": q, "user_id": uid, "role": role}

def get_user_file(filename: str, user_id: Optional[str] = None) -> Path:
    ensure_directories()
    if user_id and user_id not in ["admin", "user_admin"]:
        user_dir = DATA_DIR / "users" / user_id
        user_dir.mkdir(parents=True, exist_ok=True)
        return user_dir / filename
    return DATA_DIR / filename

def add_log(level: str, message: str, user_id: Optional[str] = None) -> Dict[str, Any]:
    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "level": level.upper(),
        "message": message,
        "user_id": user_id or "admin"
    }
    GLOBAL_LOG_HISTORY.append(entry)
    if len(GLOBAL_LOG_HISTORY) > 500:
        GLOBAL_LOG_HISTORY.pop(0)

    u_key = user_id or "admin"
    if u_key not in USER_LOG_HISTORIES:
        USER_LOG_HISTORIES[u_key] = []
    USER_LOG_HISTORIES[u_key].append(entry)
    if len(USER_LOG_HISTORIES[u_key]) > 200:
        USER_LOG_HISTORIES[u_key].pop(0)

    # Persist log to user file
    try:
        log_file = get_user_file("logs.json", user_id)
        with open(log_file, "w", encoding="utf-8") as f:
            json.dump(USER_LOG_HISTORIES[u_key][-100:], f, indent=2, ensure_ascii=False)
    except Exception:
        pass

    # Notify live subscribers
    dead_subscribers = []
    for sub in LOG_LISTENERS:
        try:
            # Send if subscriber is admin OR matches user_id
            if sub.get("role") == "admin" or sub.get("user_id") == u_key or not sub.get("user_id"):
                sub["queue"].put_nowait(entry)
        except Exception:
            dead_subscribers.append(sub)

    for ds in dead_subscribers:
        if ds in LOG_LISTENERS:
            LOG_LISTENERS.remove(ds)

    try:
        print(f"[{entry['timestamp']}] [{entry['level']}] [{u_key}] {entry['message']}")
    except Exception:
        pass

    return entry

def get_logs(limit: int = 100, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    # Admin / System logs
    if not user_id or user_id in ["admin", "user_admin"]:
        if not GLOBAL_LOG_HISTORY:
            log_file = DATA_DIR / "logs.json"
            if log_file.exists():
                try:
                    with open(log_file, "r", encoding="utf-8") as f:
                        entries = json.load(f)
                        GLOBAL_LOG_HISTORY.extend(entries)
                except Exception:
                    pass
        return GLOBAL_LOG_HISTORY[-limit:]

    # Tenant-specific logs
    u_key = user_id
    if u_key not in USER_LOG_HISTORIES or not USER_LOG_HISTORIES[u_key]:
        log_file = get_user_file("logs.json", user_id)
        if log_file.exists():
            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    USER_LOG_HISTORIES[u_key] = json.load(f)
            except Exception:
                pass
    history = USER_LOG_HISTORIES.get(u_key, [])
    return history[-limit:]

def subscribe_logs(user_id: Optional[str] = None, role: str = "user") -> asyncio.Queue:
    q = asyncio.Queue()
    LOG_LISTENERS.append({"queue": q, "user_id": user_id, "role": role})
    return q

def unsubscribe_logs(q: asyncio.Queue):
    for sub in list(LOG_LISTENERS):
        if sub["queue"] is q:
            LOG_LISTENERS.remove(sub)

# ----------------- ACCOUNTS -----------------

def get_accounts(user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    ensure_directories()
    f = get_user_file("accounts.json", user_id)
    if f.exists():
        try:
            with open(f, "r", encoding="utf-8") as fp:
                return json.load(fp)
        except Exception:
            return []
    return []

def save_accounts(accounts: List[Dict[str, Any]], user_id: Optional[str] = None) -> None:
    ensure_directories()
    f = get_user_file("accounts.json", user_id)
    with open(f, "w", encoding="utf-8") as fp:
        json.dump(accounts, fp, indent=2, ensure_ascii=False)

def get_account_by_id(account_id: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    accounts = get_accounts(user_id)
    for acc in accounts:
        if acc.get("id") == account_id:
            return acc
    return None

def upsert_account(account_data: Dict[str, Any], user_id: Optional[str] = None) -> None:
    accounts = get_accounts(user_id)
    acc_id = account_data.get("id")
    found = False
    for i, acc in enumerate(accounts):
        if acc.get("id") == acc_id or acc.get("phone") == account_data.get("phone"):
            accounts[i] = {**acc, **account_data}
            found = True
            break
    if not found:
        accounts.append(account_data)
    save_accounts(accounts, user_id)

def delete_account(account_id: str, user_id: Optional[str] = None) -> bool:
    accounts = get_accounts(user_id)
    account_to_remove = None
    for acc in accounts:
        if acc.get("id") == account_id:
            account_to_remove = acc
            break
    if account_to_remove:
        accounts.remove(account_to_remove)
        save_accounts(accounts, user_id)
        session_file = SESSIONS_DIR / f"{account_id}.session"
        if session_file.exists():
            try:
                os.remove(session_file)
            except Exception:
                pass
        return True
    return False

def toggle_account_active(account_id: str, user_id: Optional[str] = None) -> Optional[bool]:
    accounts = get_accounts(user_id)
    for acc in accounts:
        if acc.get("id") == account_id:
            acc["is_active"] = not acc.get("is_active", True)
            save_accounts(accounts, user_id)
            return acc["is_active"]
    return None

# ----------------- TARGET GROUPS -----------------

def get_groups(user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    ensure_directories()
    f = get_user_file("groups.json", user_id)
    if f.exists():
        try:
            with open(f, "r", encoding="utf-8") as fp:
                return json.load(fp)
        except Exception:
            return []
    return []

def save_groups(groups: List[Dict[str, Any]], user_id: Optional[str] = None) -> None:
    ensure_directories()
    f = get_user_file("groups.json", user_id)
    with open(f, "w", encoding="utf-8") as fp:
        json.dump(groups, fp, indent=2, ensure_ascii=False)

def import_groups_from_text(raw_text: str, user_id: Optional[str] = None) -> int:
    lines = raw_text.strip().splitlines()
    existing = {g["target"]: g for g in get_groups(user_id)}
    count = 0
    for line in lines:
        cleaned = sanitize_group_target(line)
        if cleaned and cleaned not in existing:
            existing[cleaned] = {
                "target": cleaned,
                "status": "Ready",
                "last_posted": None,
                "last_result": None,
                "fail_count": 0
            }
            count += 1
    save_groups(list(existing.values()), user_id)
    return count

def update_group_status(target: str, status: str, result: Optional[str] = None, success: bool = True, user_id: Optional[str] = None):
    groups = get_groups(user_id)
    for g in groups:
        if g["target"] == target:
            g["status"] = status
            g["last_posted"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            g["last_result"] = result or status
            if success:
                g["fail_count"] = 0
            else:
                g["fail_count"] = g.get("fail_count", 0) + 1
            break
    save_groups(groups, user_id)

def clear_all_groups(user_id: Optional[str] = None) -> None:
    save_groups([], user_id)

# ----------------- MULTI-POST POOL (CAMPAIGNS) -----------------

DEFAULT_POSTS = [
    {
        "id": "post_1",
        "title": "Post #1 (Welcome Announcement)",
        "html_content": "🔥 <b>Welcome to our exclusive community!</b>\nCheck out the daily updates and announcements 🚀\n<i>Powered by Telegram Automation</i>",
        "media_path": None,
        "media_name": None,
        "has_media": False,
        "source_chat": None,
        "source_msg_id": None,
        "custom_emojis_count": 0,
        "is_active": True,
        "assigned_account": "all",
        "times_posted": 0,
        "last_used": None,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    },
    {
        "id": "post_2",
        "title": "Post #2 (Special Deals & Offers)",
        "html_content": "💎 <b>Exclusive Deals & VIP Insights!</b>\nDon't miss our latest community promotions and insights ⭐\nJoin the discussion below 👇",
        "media_path": None,
        "media_name": None,
        "has_media": False,
        "custom_emojis_count": 0,
        "is_active": True,
        "assigned_account": "all",
        "times_posted": 0,
        "last_used": None,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    },
    {
        "id": "post_3",
        "title": "Post #3 (Daily Digest & Tips)",
        "html_content": "⚡ <b>Daily Quick Digest!</b>\nStay ahead of the game with our verified updates and news 🌟\nFeel free to ask questions in chat!",
        "media_path": None,
        "media_name": None,
        "has_media": False,
        "custom_emojis_count": 0,
        "is_active": True,
        "assigned_account": "all",
        "times_posted": 0,
        "last_used": None,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
]

def get_posts(user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    ensure_directories()
    f = get_user_file("posts.json", user_id)
    if f.exists():
        try:
            with open(f, "r", encoding="utf-8") as fp:
                posts = json.load(fp)
                if isinstance(posts, list) and posts:
                    return posts
        except Exception:
            pass

    # Provide default posts if fresh user
    initial = [dict(p) for p in DEFAULT_POSTS]
    save_posts(initial, user_id)
    return initial

def save_posts(posts: List[Dict[str, Any]], user_id: Optional[str] = None) -> None:
    ensure_directories()
    f = get_user_file("posts.json", user_id)
    with open(f, "w", encoding="utf-8") as fp:
        json.dump(posts, fp, indent=2, ensure_ascii=False)

def get_active_posts(user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    posts = get_posts(user_id)
    active = [p for p in posts if p.get("is_active", True)]
    return active if active else (posts[:1] if posts else DEFAULT_POSTS[:1])

def get_post_by_id(post_id: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    posts = get_posts(user_id)
    for p in posts:
        if p.get("id") == post_id:
            return p
    return None

def upsert_post(post_data: Dict[str, Any], user_id: Optional[str] = None) -> Dict[str, Any]:
    posts = get_posts(user_id)
    pid = post_data.get("id")
    if not pid:
        pid = f"post_{int(time.time())}"
        post_data["id"] = pid

    found = False
    for i, p in enumerate(posts):
        if p.get("id") == pid:
            posts[i] = {**p, **post_data, "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
            found = True
            break
    if not found:
        post_data.setdefault("times_posted", 0)
        post_data.setdefault("is_active", True)
        post_data.setdefault("assigned_account", "all")
        post_data.setdefault("created_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        posts.append(post_data)

    save_posts(posts, user_id)
    return post_data

def delete_post(post_id: str, user_id: Optional[str] = None) -> bool:
    posts = get_posts(user_id)
    initial_len = len(posts)
    posts = [p for p in posts if p.get("id") != post_id]
    if len(posts) < initial_len:
        save_posts(posts, user_id)
        return True
    return False

def toggle_post_active(post_id: str, user_id: Optional[str] = None) -> Optional[bool]:
    posts = get_posts(user_id)
    for p in posts:
        if p.get("id") == post_id:
            p["is_active"] = not p.get("is_active", True)
            save_posts(posts, user_id)
            return p["is_active"]
    return None

def increment_post_usage(post_id: str, user_id: Optional[str] = None):
    posts = get_posts(user_id)
    for p in posts:
        if p.get("id") == post_id:
            p["times_posted"] = p.get("times_posted", 0) + 1
            p["last_used"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            break
    save_posts(posts, user_id)

def resolve_post_media_path(post: Optional[Dict[str, Any]]) -> Optional[str]:
    """
    Cross-platform resolver for post media attachments.
    Resolves absolute path, relative path, or filename inside MEDIA_DIR.
    Handles cross-OS migrations (Windows <-> Linux container on Render).
    """
    if not post or not post.get("has_media"):
        return None

    candidates = []

    # 1. Direct media_path if provided
    raw_path = post.get("media_path")
    if raw_path:
        candidates.append(str(raw_path))
        base_name = os.path.basename(str(raw_path).replace("\\", "/"))
        candidates.append(str(MEDIA_DIR / base_name))

    # 2. media_filename if provided
    media_fn = post.get("media_filename")
    if media_fn:
        clean_fn = os.path.basename(str(media_fn).replace("\\", "/"))
        candidates.append(str(MEDIA_DIR / clean_fn))

    # 3. media_url if provided (e.g. /media/post_123.jpg)
    media_url = post.get("media_url")
    if media_url and isinstance(media_url, str):
        url_fn = os.path.basename(media_url.replace("\\", "/"))
        candidates.append(str(MEDIA_DIR / url_fn))

    # 4. Fallback to media_name if present
    media_nm = post.get("media_name")
    if media_nm:
        clean_nm = os.path.basename(str(media_nm).replace("\\", "/"))
        candidates.append(str(MEDIA_DIR / clean_nm))

    for p in candidates:
        if p and os.path.isfile(p):
            return os.path.abspath(p)

    return None

# Draft helper
DEFAULT_DRAFT = DEFAULT_POSTS[0]

def get_draft(user_id: Optional[str] = None) -> Dict[str, Any]:
    posts = get_active_posts(user_id)
    return posts[0] if posts else DEFAULT_DRAFT

def save_draft(draft: Dict[str, Any], user_id: Optional[str] = None) -> None:
    upsert_post(draft, user_id)
