import asyncio
import os
import random
import time
from typing import Dict, Any, List, Optional

from telethon.errors import (
    FloodWaitError,
    ChatWriteForbiddenError,
    UserBannedInChannelError,
    SlowModeWaitError,
    ChannelPrivateError,
    InviteHashExpiredError,
    InviteHashInvalidError,
    UserAlreadyParticipantError
)
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest, CheckChatInviteRequest

from app.config import load_config
from app.storage import (
    get_accounts,
    upsert_account,
    get_groups,
    update_group_status,
    get_draft,
    get_active_posts,
    increment_post_usage,
    add_log
)
from app.auth_mgr import get_user_by_id
from app.emoji_parser import parse_html_to_message_and_entities
from app.telegram_mgr import get_client_for_account

_user_broadcasting: Dict[str, bool] = {}
_user_account_cursors: Dict[str, int] = {}
_user_post_cursors: Dict[str, int] = {}
_user_broadcast_stats: Dict[str, Dict[str, Any]] = {}

def get_broadcast_stats(user_id: Optional[str] = None) -> Dict[str, Any]:
    u_key = user_id or "admin"
    if u_key not in _user_broadcast_stats:
        _user_broadcast_stats[u_key] = {
            "total_cycles": 0,
            "last_run_time": None,
            "total_sent": 0,
            "total_failed": 0
        }
    return {
        **_user_broadcast_stats[u_key],
        "is_broadcasting": _user_broadcasting.get(u_key, False)
    }

def get_next_available_account(accounts: List[Dict[str, Any]], rotation_mode: str = "round_robin", user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    u_key = user_id or "admin"
    now = time.time()
    valid_accounts = [
        acc for acc in accounts
        if acc.get("is_active", True) and (not acc.get("flood_wait_until") or acc.get("flood_wait_until") <= now)
    ]

    if not valid_accounts:
        return None

    if rotation_mode == "random":
        return random.choice(valid_accounts)

    # Round Robin
    cursor = _user_account_cursors.get(u_key, 0) % len(valid_accounts)
    selected = valid_accounts[cursor]
    _user_account_cursors[u_key] = (cursor + 1) % len(valid_accounts)
    return selected

def reset_broadcasting_flag(user_id: Optional[str] = None):
    u_key = user_id or "admin"
    _user_broadcasting[u_key] = False

async def resolve_target_entity(client, target: str, auto_join: bool = True, user_id: Optional[str] = None):
    target = target.strip()
    if target.startswith("https://t.me/+") or target.startswith("https://t.me/joinchat/"):
        hash_key = target.split("+")[-1] if "+" in target else target.split("joinchat/")[-1]
        hash_key = hash_key.strip("/")
        if auto_join:
            try:
                await client(ImportChatInviteRequest(hash_key))
                add_log("INFO", f"Joined private group via invite link: {target}", user_id=user_id)
            except UserAlreadyParticipantError:
                pass
            except Exception as e:
                add_log("WARNING", f"Invite link note for {target}: {e}", user_id=user_id)
        return await client.get_entity(target)

    # Check if numeric ID
    if (target.startswith("-") and target[1:].isdigit()) or target.isdigit():
        target_entity = await client.get_entity(int(target))
    else:
        # Try resolving username with or without @
        try:
            target_entity = await client.get_entity(target)
        except Exception:
            if not target.startswith("@"):
                target_entity = await client.get_entity(f"@{target}")
            else:
                raise

    if auto_join:
        try:
            await client(JoinChannelRequest(target_entity))
        except UserAlreadyParticipantError:
            pass
        except Exception:
            pass

    return target_entity

async def run_single_broadcast(user_id: Optional[str] = None, is_manual: bool = False) -> Dict[str, Any]:
    """
    Executes one full broadcast run across all target groups using multi-account rotation.
    Preserves 3D custom animated emojis, media attachments, and anti-flood delays.
    Fully isolated per user tenant.
    """
    u_key = user_id or "admin"

    # Verify subscription if regular user
    if user_id and user_id not in ["admin", "user_admin"]:
        user = get_user_by_id(user_id)
        if not user or user.get("is_locked") or user.get("status") != "active":
            add_log("ERROR", f"Broadcast rejected: Monthly subscription is {user.get('status', 'inactive') if user else 'not found'}. Services locked.", user_id=user_id)
            return {"status": "error", "message": "Subscription expired or locked. Please contact admin to renew."}

    if _user_broadcasting.get(u_key, False):
        add_log("WARNING", "A broadcast is already actively running. Skipping this trigger.", user_id=user_id)
        return {"status": "busy", "message": "Broadcast in progress"}

    _user_broadcasting[u_key] = True

    if u_key not in _user_broadcast_stats:
        _user_broadcast_stats[u_key] = {
            "total_cycles": 0,
            "last_run_time": None,
            "total_sent": 0,
            "total_failed": 0
        }
    stats = _user_broadcast_stats[u_key]

    try:
        config = load_config(user_id)
        accounts = get_accounts(user_id)
        groups = get_groups(user_id)

        if not accounts:
            add_log("ERROR", "Broadcast aborted: No Telegram accounts configured.", user_id=user_id)
            return {"status": "error", "message": "No accounts added."}

        # Filter by selected account if a specific account is chosen
        selected_account = config.get("selected_account", "all")
        if selected_account and selected_account != "all":
            if isinstance(selected_account, list):
                filtered_accounts = [a for a in accounts if a.get("id") in selected_account or str(a.get("user_id")) in [str(x) for x in selected_account]]
            else:
                filtered_accounts = [a for a in accounts if a.get("id") == selected_account or str(a.get("user_id")) == str(selected_account) or a.get("phone") == selected_account]

            if filtered_accounts:
                accounts = filtered_accounts
                acc_label = f"{accounts[0].get('first_name', '')} (@{accounts[0].get('username') or accounts[0].get('phone')})"
                add_log("INFO", f"Selected Account Active: Broadcasting exclusively via '{acc_label}'", user_id=user_id)
            else:
                add_log("WARNING", f"Selected account '{selected_account}' not found. Falling back to all accounts.", user_id=user_id)

        if not groups:
            add_log("ERROR", "Broadcast aborted: Target groups list is empty.", user_id=user_id)
            return {"status": "error", "message": "No target groups configured."}

        active_posts = get_active_posts(user_id)
        if not active_posts:
            add_log("ERROR", "Broadcast aborted: No active posts found in the campaign pool.", user_id=user_id)
            return {"status": "error", "message": "No active posts configured."}

        # Filter posts if user selected specific posts in schedule
        selected_posts_cfg = config.get("selected_posts", "all")
        if selected_posts_cfg and selected_posts_cfg != "all":
            if isinstance(selected_posts_cfg, list) and len(selected_posts_cfg) > 0:
                filtered_posts = [p for p in active_posts if p.get("id") in selected_posts_cfg]
                if filtered_posts:
                    active_posts = filtered_posts
                    add_log("INFO", f"Post Selection Active: Using {len(active_posts)} selected campaign post(s)", user_id=user_id)

        post_rotation_mode = config.get("post_rotation_mode", "random")
        post_scope = config.get("post_selection_scope", "per_group")

        def get_posts_for_account(post_pool: List[Dict[str, Any]], acc: Dict[str, Any]) -> List[Dict[str, Any]]:
            acc_id = str(acc.get("id", ""))
            acc_uid = str(acc.get("user_id", ""))
            phone = str(acc.get("phone", ""))
            eligible = []
            for p in post_pool:
                assigned = p.get("assigned_account")
                if not assigned or assigned == "all":
                    eligible.append(p)
                elif isinstance(assigned, list):
                    if any(str(x) in (acc_id, acc_uid, phone) for x in assigned):
                        eligible.append(p)
                elif str(assigned) in (acc_id, acc_uid, phone):
                    eligible.append(p)
            return eligible if eligible else post_pool

        # If scope is per_cycle, pick one post for this whole run
        post_cursor = _user_post_cursors.get(u_key, 0)
        if post_rotation_mode == "random":
            cycle_post = random.choice(active_posts)
        else:
            cycle_post = active_posts[post_cursor % len(active_posts)]
            _user_post_cursors[u_key] = post_cursor + 1

        if len(accounts) == 1:
            acc_info = f"Single Account: '{accounts[0].get('first_name', '')}' (@{accounts[0].get('username') or accounts[0].get('phone')})"
        else:
            acc_info = f"{len(accounts)} Accounts ({config.get('account_rotation', 'round_robin').upper()})"

        add_log("INFO", f"=== Starting {'Manual' if is_manual else 'Scheduled'} Broadcast ===", user_id=user_id)
        add_log("INFO", f"Targets: {len(groups)} groups | Active Post Pool: {len(active_posts)} | Sender: {acc_info} | Post Rotation: {post_rotation_mode.upper()} ({post_scope})", user_id=user_id)

        sent_count = 0
        failed_count = 0
        rotation_mode = config.get("account_rotation", "round_robin")
        auto_join = config.get("auto_join_groups", True)
        delay_min = max(2, int(config.get("random_delay_min", 10)))
        delay_max = max(delay_min, int(config.get("random_delay_max", 25)))

        for i, grp in enumerate(groups):
            target = grp["target"]
            account = get_next_available_account(accounts, rotation_mode, user_id=user_id)

            if not account:
                add_log("ERROR", "All available accounts are on FloodWait or inactive. Halting current cycle.", user_id=user_id)
                break

            client = await get_client_for_account(account)
            if not client:
                add_log("WARNING", f"Could not connect client for account {account.get('phone')}. Skipping account.", user_id=user_id)
                continue

            # Select eligible post for this account & group
            account_posts = get_posts_for_account(active_posts, account)
            if not account_posts:
                account_posts = active_posts

            if post_scope == "per_group":
                if post_rotation_mode == "random":
                    post_to_send = random.choice(account_posts)
                else:
                    curr_pc = _user_post_cursors.get(u_key, 0)
                    post_to_send = account_posts[curr_pc % len(account_posts)]
                    _user_post_cursors[u_key] = curr_pc + 1
            else:
                post_to_send = cycle_post if cycle_post in account_posts else account_posts[0]

            html_content = post_to_send.get("html_content", "").strip()
            text, entities = parse_html_to_message_and_entities(html_content)
            media_path = post_to_send.get("media_path") if (post_to_send.get("has_media") and post_to_send.get("media_path") and os.path.exists(post_to_send.get("media_path"))) else None
            post_title = post_to_send.get("title", post_to_send.get("id"))

            acc_label = f"{account.get('first_name', '')} (@{account.get('username') or account.get('phone')})"
            add_log("INFO", f"[{i+1}/{len(groups)}] Sending '{post_title}' to '{target}' via {acc_label}...", user_id=user_id)

            try:
                # Resolve target entity safely
                target_entity = await resolve_target_entity(client, target, auto_join, user_id=user_id)

                # Dispatch message with full entities (preserving 3D custom emojis) & media
                if media_path:
                    await client.send_message(target_entity, text, formatting_entities=entities, file=media_path)
                else:
                    await client.send_message(target_entity, text, formatting_entities=entities)

                increment_post_usage(post_to_send.get("id", "post_1"), user_id=user_id)
                update_group_status(target, "Success", result=f"Sent: {post_title}", success=True, user_id=user_id)
                sent_count += 1
                stats["total_sent"] += 1
                add_log("SUCCESS", f"Successfully posted '{post_title}' to {target}!", user_id=user_id)

            except FloodWaitError as e:
                add_log("WARNING", f"FloodWaitError: Account {acc_label} must wait {e.seconds}s.", user_id=user_id)
                account["flood_wait_until"] = time.time() + e.seconds
                upsert_account(account, user_id=user_id)
                update_group_status(target, "FloodWait", result=f"Flood wait {e.seconds}s", success=False, user_id=user_id)
                failed_count += 1
                stats["total_failed"] += 1

            except (ChatWriteForbiddenError, UserBannedInChannelError) as e:
                add_log("WARNING", f"Cannot post to {target}: Write forbidden or restricted in group.", user_id=user_id)
                update_group_status(target, "Restricted", result="Write forbidden/restricted", success=False, user_id=user_id)
                failed_count += 1
                stats["total_failed"] += 1

            except SlowModeWaitError as e:
                add_log("WARNING", f"Slow mode active in {target}: Requires {e.seconds}s wait.", user_id=user_id)
                update_group_status(target, "SlowMode", result=f"SlowMode: {e.seconds}s wait", success=False, user_id=user_id)
                failed_count += 1
                stats["total_failed"] += 1

            except (ChannelPrivateError, InviteHashExpiredError, InviteHashInvalidError) as e:
                add_log("WARNING", f"Target inaccessible or invite expired: {target} ({e})", user_id=user_id)
                update_group_status(target, "Invalid", result=str(e), success=False, user_id=user_id)
                failed_count += 1
                stats["total_failed"] += 1

            except Exception as e:
                add_log("ERROR", f"Failed posting to {target}: {e}", user_id=user_id)
                update_group_status(target, "Failed", result=str(e), success=False, user_id=user_id)
                failed_count += 1
                stats["total_failed"] += 1

            # Anti-flood human randomized delay between groups (unless last group)
            if i < len(groups) - 1:
                sleep_duration = random.randint(delay_min, delay_max)
                add_log("INFO", f"Anti-flood pause: waiting {sleep_duration}s before next group...", user_id=user_id)
                await asyncio.sleep(sleep_duration)

        stats["total_cycles"] += 1
        stats["last_run_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
        add_log("SUCCESS", f"=== Broadcast Finished: {sent_count} Sent | {failed_count} Failed ===", user_id=user_id)
        return {
            "status": "completed",
            "sent": sent_count,
            "failed": failed_count,
            "total": len(groups)
        }

    except Exception as e:
        add_log("ERROR", f"Broadcast fatal error: {e}", user_id=user_id)
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}

    finally:
        _user_broadcasting[u_key] = False
