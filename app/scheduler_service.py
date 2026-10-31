import asyncio
import time
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

from app.config import load_config
from app.poster_engine import run_single_broadcast, get_broadcast_stats
from app.storage import add_log
from app.auth_mgr import get_user_by_id

class UserScheduler:
    def __init__(self, user_id: Optional[str] = None):
        self.user_id = user_id
        self.is_running: bool = False
        self.next_run_timestamp: Optional[float] = None
        self.stop_event = asyncio.Event()
        self.task: Optional[asyncio.Task] = None

_user_schedulers: Dict[str, UserScheduler] = {}

def get_user_scheduler(user_id: Optional[str] = None) -> UserScheduler:
    u_key = user_id or "admin"
    if u_key not in _user_schedulers:
        _user_schedulers[u_key] = UserScheduler(user_id)
    return _user_schedulers[u_key]

def get_interval_seconds(config: Dict[str, Any]) -> int:
    val = float(config.get("interval_value", 2))
    unit = config.get("interval_unit", "hours").lower()
    if unit in ["minute", "minutes", "m"]:
        return max(60, int(val * 60))
    # Default to hours
    return max(300, int(val * 3600))

def get_scheduler_status(user_id: Optional[str] = None) -> Dict[str, Any]:
    sched = get_user_scheduler(user_id)
    config = load_config(user_id)
    now = time.time()
    remaining = 0
    next_run_str = None

    if sched.is_running and sched.next_run_timestamp:
        remaining = max(0, int(sched.next_run_timestamp - now))
        next_run_str = datetime.fromtimestamp(sched.next_run_timestamp).strftime("%Y-%m-%d %H:%M:%S")

    interval_sec = get_interval_seconds(config)
    hours = interval_sec / 3600

    return {
        "is_running": sched.is_running,
        "interval_value": config.get("interval_value", 2),
        "interval_unit": config.get("interval_unit", "hours"),
        "interval_seconds": interval_sec,
        "interval_hours_formatted": f"{hours:.2f}".rstrip("0").rstrip("."),
        "next_run_timestamp": sched.next_run_timestamp,
        "next_run_time": next_run_str,
        "remaining_seconds": remaining,
        "stats": get_broadcast_stats(user_id)
    }

async def _scheduler_loop(sched: UserScheduler):
    u_label = sched.user_id or "admin"
    sched.stop_event.clear()
    add_log("INFO", f"=== Telegram Posting Automation Scheduler STARTED for tenant [{u_label}] ===", user_id=sched.user_id)

    while sched.is_running:
        # Verify user subscription before scheduling cycle
        if sched.user_id and sched.user_id not in ["admin", "user_admin"]:
            user = get_user_by_id(sched.user_id)
            if not user or user.get("is_locked") or user.get("status") != "active":
                add_log("WARNING", "Monthly subscription expired or inactive. Automatically pausing posting scheduler.", user_id=sched.user_id)
                sched.is_running = False
                break

        config = load_config(sched.user_id)
        interval_sec = get_interval_seconds(config)
        
        # Schedule next cycle
        sched.next_run_timestamp = time.time() + interval_sec
        next_dt = datetime.fromtimestamp(sched.next_run_timestamp).strftime("%H:%M:%S")
        add_log("INFO", f"Scheduler waiting for next cycle at {next_dt} (in {interval_sec // 60} minutes)...", user_id=sched.user_id)

        # Wait with interrupt capability
        try:
            while time.time() < sched.next_run_timestamp:
                if not sched.is_running or sched.stop_event.is_set():
                    break
                sleep_chunk = min(2, sched.next_run_timestamp - time.time())
                await asyncio.sleep(sleep_chunk)

            if not sched.is_running or sched.stop_event.is_set():
                break

            # Execute scheduled broadcast
            add_log("INFO", "Scheduled posting interval reached! Launching broadcast cycle...", user_id=sched.user_id)
            await run_single_broadcast(user_id=sched.user_id, is_manual=False)

        except asyncio.CancelledError:
            break
        except Exception as e:
            add_log("ERROR", f"Scheduler loop error: {e}", user_id=sched.user_id)
            await asyncio.sleep(10)

    sched.is_running = False
    sched.next_run_timestamp = None
    add_log("INFO", f"=== Telegram Posting Automation Scheduler STOPPED for tenant [{u_label}] ===", user_id=sched.user_id)

def start_scheduler(user_id: Optional[str] = None) -> Dict[str, Any]:
    if user_id and user_id not in ["admin", "user_admin"]:
        user = get_user_by_id(user_id)
        if not user or user.get("is_locked") or user.get("status") != "active":
            return {"status": "error", "message": f"Your monthly subscription is {user.get('status', 'inactive') if user else 'not found'}. Services are locked. Please contact admin."}

    sched = get_user_scheduler(user_id)
    if sched.is_running:
        return {"status": "already_running", "message": "Scheduler is already active."}

    sched.is_running = True
    sched.stop_event.clear()
    loop = asyncio.get_event_loop()
    sched.task = loop.create_task(_scheduler_loop(sched))
    return {"status": "started", "message": "Scheduler started successfully."}

def stop_scheduler(user_id: Optional[str] = None) -> Dict[str, Any]:
    sched = get_user_scheduler(user_id)
    if not sched.is_running:
        return {"status": "not_running", "message": "Scheduler is not currently running."}

    sched.is_running = False
    sched.stop_event.set()
    sched.next_run_timestamp = None
    if sched.task and not sched.task.done():
        sched.task.cancel()
    return {"status": "stopped", "message": "Scheduler stopped."}

async def trigger_now(user_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Manually triggers an immediate broadcast cycle.
    """
    if user_id and user_id not in ["admin", "user_admin"]:
        user = get_user_by_id(user_id)
        if not user or user.get("is_locked") or user.get("status") != "active":
            return {"status": "error", "message": f"Subscription {user.get('status', 'inactive') if user else 'not found'}. Services are locked. Please contact admin."}

    add_log("INFO", "Manual 'Post Now' triggered by user.", user_id=user_id)
    result = await run_single_broadcast(user_id=user_id, is_manual=True)
    return result
