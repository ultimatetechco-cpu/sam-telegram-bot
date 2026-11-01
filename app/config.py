import json
import os
from pathlib import Path
from typing import Dict, Any, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
MEDIA_DIR = DATA_DIR / "media"
SESSIONS_DIR = BASE_DIR / "sessions"

CONFIG_FILE = DATA_DIR / "config.json"
ACCOUNTS_FILE = DATA_DIR / "accounts.json"
GROUPS_FILE = DATA_DIR / "groups.json"
DRAFT_FILE = DATA_DIR / "draft.json"
POSTS_FILE = DATA_DIR / "posts.json"
LOGS_FILE = DATA_DIR / "logs.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "interval_value": 2,
    "interval_unit": "hours",       # "hours" or "minutes"
    "random_delay_min": 10,         # Seconds between sending to each group
    "random_delay_max": 25,         # Max delay
    "auto_join_groups": True,       # Auto join group if client not yet member
    "account_rotation": "round_robin",
    "selected_account": "all",      # "all" or specific account ID e.g. "acc_1056549064"
    "selected_posts": "all",        # "all" or list of post IDs e.g. ["post_1", "post_2"]
    "post_rotation_mode": "random", # "random" or "sequential"
    "post_selection_scope": "per_group", # "per_group" (alternate each group) or "per_cycle"
    "web_port": 8080,
    "web_host": "0.0.0.0"
}

def ensure_directories():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "users").mkdir(parents=True, exist_ok=True)

def get_user_data_dir(user_id: Optional[str] = None) -> Path:
    ensure_directories()
    if user_id and user_id not in ["admin", "user_admin"]:
        u_dir = DATA_DIR / "users" / user_id
        u_dir.mkdir(parents=True, exist_ok=True)
        return u_dir
    return DATA_DIR

def load_config(user_id: Optional[str] = None) -> Dict[str, Any]:
    ensure_directories()
    cfg_file = get_user_data_dir(user_id) / "config.json"
    if cfg_file.exists():
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {**DEFAULT_CONFIG, **data}
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()

def save_config(cfg: Dict[str, Any], user_id: Optional[str] = None, sync_vault: bool = True) -> None:
    ensure_directories()
    cfg_file = get_user_data_dir(user_id) / "config.json"
    with open(cfg_file, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    if sync_vault:
        try:
            from app.cloud_vault import schedule_vault_push
            schedule_vault_push()
        except Exception:
            pass
