import asyncio
import json
import os
import re
import shutil
import time
import mimetypes
from pathlib import Path
from typing import Dict, Any, Optional, List

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Depends, Header, Cookie, Response
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import (
    load_config,
    save_config,
    MEDIA_DIR,
    BASE_DIR
)
from app.storage import (
    get_accounts,
    delete_account,
    toggle_account_active,
    get_groups,
    import_groups_from_text,
    clear_all_groups,
    get_draft,
    save_draft,
    get_posts,
    get_post_by_id,
    upsert_post,
    delete_post,
    toggle_post_active,
    get_active_posts,
    resolve_post_media_path,
    get_logs,
    subscribe_logs,
    unsubscribe_logs,
    add_log
)
from app.auth_mgr import (
    init_auth,
    login_user,
    register_user,
    logout_user,
    authenticate_token,
    get_user_by_id,
    get_user_by_username,
    get_user_by_email,
    verify_password,
    sanitize_user,
    admin_approve_user,
    admin_extend_subscription,
    admin_update_user_plan,
    admin_set_user_status,
    admin_delete_user,
    get_all_users_for_admin,
    admin_change_password,
    admin_update_email,
    PLANS
)
from app.telegram_mgr import (
    start_login,
    submit_login_code,
    resend_code_sms,
    login_with_string_session,
    fetch_telegram_post,
    fetch_account_groups,
    import_account_groups
)
from app.scheduler_service import (
    get_scheduler_status,
    start_scheduler,
    stop_scheduler,
    trigger_now
)
from app.emoji_parser import extract_custom_emojis, parse_html_to_message_and_entities
from app.email_service import (
    generate_otp,
    store_otp,
    verify_otp,
    mask_email,
    dispatch_otp_email,
    load_email_config,
    save_email_config,
    send_email_via_smtp
)
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Telegram Automation Hub & SaaS Portal", version="2.0.0")

# Enable CORS for cross-domain and Hostinger proxy access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Startup event to ensure auth database is initialized and Cloud Vault sync is active
@app.on_event("startup")
async def startup_event():
    init_auth()
    try:
        from app.cloud_vault import start_cloud_vault_service
        asyncio.create_task(start_cloud_vault_service())
    except Exception as e:
        print(f"Failed to start Cloud Vault service: {e}")

# Ensure media directory exists
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

# Mount static files
STATIC_DIR = BASE_DIR / "app" / "web" / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Explicit media serving routes with MIME detection and caching
@app.get("/media/{filename:path}")
@app.get("/api/media/{filename:path}")
async def serve_media_file(filename: str):
    clean_name = os.path.basename(filename.replace("\\", "/"))
    file_path = MEDIA_DIR / clean_name
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Media file not found")
    mime_type, _ = mimetypes.guess_type(str(file_path))
    return FileResponse(
        file_path,
        media_type=mime_type or "application/octet-stream",
        headers={"Cache-Control": "public, max-age=86400"}
    )

# ----------------- AUTH DEPENDENCIES -----------------

async def get_current_user(
    request: Request,
    authorization: Optional[str] = Header(None)
) -> Dict[str, Any]:
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
    elif "auth_token" in request.cookies:
        token = request.cookies.get("auth_token")
    elif "token" in request.query_params:
        token = request.query_params.get("token")

    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")

    user = authenticate_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    return user

async def get_admin_user(current_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Super Admin access required")
    return current_user

# ----------------- AUTHENTICATION ROUTES -----------------

@app.get("/api/auth/plans")
async def api_get_plans():
    return PLANS

class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str
    plan_tier: str = "tier_1"

class RegisterVerifyOtpRequest(BaseModel):
    email: str
    otp: str

class LoginRequest(BaseModel):
    username: str
    password: str

class LoginVerifyOtpRequest(BaseModel):
    user_id: str
    otp: str

class ResendOtpRequest(BaseModel):
    target: str
    purpose: str = "registration"

# 1. Register: Step 1 - Validate & Dispatch OTP to Email
@app.post("/api/auth/register/request_otp")
async def api_register_request_otp(req: RegisterRequest):
    try:
        username = req.username.strip()
        email = req.email.strip().lower()
        if not username or len(username) < 3:
            raise HTTPException(status_code=400, detail="Username must be at least 3 characters.")
        if not email or "@" not in email:
            raise HTTPException(status_code=400, detail="Please enter a valid email address.")
        if not req.password or len(req.password) < 6:
            raise HTTPException(status_code=400, detail="Password must be at least 6 characters.")
        if get_user_by_username(username):
            raise HTTPException(status_code=400, detail="Username is already taken. Please choose another.")
        if get_user_by_email(email):
            raise HTTPException(status_code=400, detail="Email is already registered. Please sign in.")

        otp = generate_otp()
        payload = {
            "username": username,
            "email": email,
            "password": req.password,
            "plan_tier": req.plan_tier
        }
        store_otp(email, otp, "registration", payload)
        
        # Dispatch OTP to the given email
        success, msg = dispatch_otp_email(email, otp, "registration")
        add_log("INFO", f"Sent registration verification code to {mask_email(email)} (Status: {'Sent' if success else 'Pending'})", user_id="admin")
        
        return {
            "status": "otp_sent",
            "email": email,
            "masked_email": mask_email(email),
            "message": f"Verification code sent to {mask_email(email)}. Please check your inbox and spam folder."
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 2. Register: Step 2 - Verify OTP & Create Tenant Account
@app.post("/api/auth/register/verify_otp")
async def api_register_verify_otp(req: RegisterVerifyOtpRequest, response: Response):
    try:
        email = req.email.strip().lower()
        is_valid, payload, err = verify_otp(email, req.otp, "registration")
        if not is_valid or not payload:
            raise HTTPException(status_code=400, detail=err or "Invalid or expired verification code.")

        reg_data = register_user(
            payload["username"],
            payload["email"],
            payload["password"],
            payload.get("plan_tier", "tier_1")
        )
        response.set_cookie(
            key="auth_token",
            value=reg_data["token"],
            httponly=False,
            max_age=30 * 86400,
            samesite="lax"
        )
        add_log("INFO", f"New tenant registered with verified email: @{reg_data['user']['username']} ({reg_data['user']['email']}) for {reg_data['user'].get('plan_info', {}).get('name')}", user_id="admin")
        return {
            "status": "success",
            "message": "Account verified and registered successfully!",
            "token": reg_data["token"],
            "user": reg_data["user"]
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 3. Login: Step 1 - Validate Credentials & Dispatch OTP to Registered Email
@app.post("/api/auth/login/request_otp")
async def api_login_request_otp(req: LoginRequest):
    try:
        user = get_user_by_username(req.username)
        if not user:
            user = get_user_by_email(req.username)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid username or password.")

        if not verify_password(req.password, user["password_hash"], user["salt"]):
            raise HTTPException(status_code=401, detail="Invalid username or password.")

        user_email = user.get("email")
        cfg = load_email_config()
        has_smtp = bool(cfg.get("smtp_password", "").strip())

        # If user has no email, or if super admin logs in before SMTP is configured:
        # Allow direct login so admin can access Admin Dashboard & set up SMTP credentials!
        if not user_email or "@" not in user_email or (user.get("role") == "admin" and not has_smtp):
            data = login_user(req.username, req.password)
            return {
                "status": "direct_login",
                "token": data["token"],
                "user": data["user"]
            }

        otp = generate_otp()
        store_otp(user["id"], otp, "login", {"user_id": user["id"], "username": user["username"]})
        
        # Dispatch OTP to user's registered email
        success, msg = dispatch_otp_email(user_email, otp, "login")
        add_log("INFO", f"Sent login security verification code to registered email for user @{user['username']}", user_id=user["id"])

        return {
            "status": "otp_sent",
            "user_id": user["id"],
            "masked_email": mask_email(user_email),
            "message": f"Security verification code sent to your registered email {mask_email(user_email)}."
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 4. Login: Step 2 - Verify Security Code & Issue Session Token
@app.post("/api/auth/login/verify_otp")
async def api_login_verify_otp(req: LoginVerifyOtpRequest, response: Response):
    try:
        is_valid, payload, err = verify_otp(req.user_id, req.otp, "login")
        if not is_valid or not payload:
            raise HTTPException(status_code=400, detail=err or "Invalid or expired security code.")

        user = get_user_by_id(req.user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User account not found.")

        import secrets
        from datetime import datetime
        from app.auth_mgr import ACTIVE_TOKENS, save_auth_sessions
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

        response.set_cookie(
            key="auth_token",
            value=token,
            httponly=False,
            max_age=30 * 86400,
            samesite="lax"
        )
        add_log("INFO", f"User @{user['username']} logged in successfully with email OTP verification.", user_id=user["id"])
        return {
            "status": "success",
            "token": token,
            "user": sanitize_user(user)
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 5. Resend OTP
@app.post("/api/auth/resend_otp")
async def api_resend_otp(req: ResendOtpRequest):
    try:
        target = req.target.strip()
        purpose = req.purpose
        otp = generate_otp()

        if purpose == "registration":
            email = target.lower()
            from app.email_service import PENDING_OTPS
            entry = PENDING_OTPS.get(email)
            payload = entry.get("payload") if entry else None
            store_otp(email, otp, "registration", payload)
            success, msg = dispatch_otp_email(email, otp, "registration")
            return {
                "status": "otp_sent",
                "masked_email": mask_email(email),
                "message": f"A new verification code was sent to {mask_email(email)}."
            }
        else: # login
            user = get_user_by_id(target)
            if not user:
                raise HTTPException(status_code=404, detail="User account not found.")
            user_email = user.get("email")
            store_otp(user["id"], otp, "login", {"user_id": user["id"], "username": user["username"]})
            success, msg = dispatch_otp_email(user_email, otp, "login")
            return {
                "status": "otp_sent",
                "masked_email": mask_email(user_email),
                "message": f"A new security code was sent to your registered email {mask_email(user_email)}."
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Direct legacy endpoints preserved for backward compatibility
@app.post("/api/auth/register")
async def api_register(req: RegisterRequest, response: Response):
    try:
        reg_data = register_user(req.username, req.email, req.password, req.plan_tier)
        response.set_cookie(
            key="auth_token",
            value=reg_data["token"],
            httponly=False,
            max_age=30 * 86400,
            samesite="lax"
        )
        add_log("INFO", f"New user registration: @{reg_data['user']['username']} ({reg_data['user']['email']}) for {reg_data['user'].get('plan_info', {}).get('name')}", user_id="admin")
        return {
            "status": "pending_approval",
            "message": "Account registered successfully! Awaiting Admin approval to activate your 1-month subscription.",
            "token": reg_data["token"],
            "user": reg_data["user"]
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/auth/login")
async def api_login(req: LoginRequest, response: Response):
    try:
        data = login_user(req.username, req.password)
        response.set_cookie(
            key="auth_token",
            value=data["token"],
            httponly=False,
            max_age=30 * 86400,
            samesite="lax"
        )
        add_log("INFO", f"User logged in: @{data['user']['username']} (Role: {data['user']['role']})", user_id=data['user']['id'])
        return data
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/auth/logout")
async def api_logout(request: Request, response: Response):
    token = request.cookies.get("auth_token")
    if not token and "Authorization" in request.headers:
        auth_hdr = request.headers["Authorization"]
        if auth_hdr.startswith("Bearer "):
            token = auth_hdr[7:].strip()
    if token:
        logout_user(token)
    response.delete_cookie("auth_token")
    return {"status": "success", "message": "Logged out successfully."}

@app.get("/api/auth/me")
async def api_me(current_user: Dict[str, Any] = Depends(get_current_user)):
    return sanitize_user(current_user)

# ----------------- ADMIN PORTAL ROUTES -----------------

@app.get("/api/admin/users")
async def api_admin_get_users(admin: Dict[str, Any] = Depends(get_admin_user)):
    users = get_all_users_for_admin()
    # Enrich with accounts count and active status
    enriched = []
    for u in users:
        uid = u["id"]
        accs = get_accounts(uid)
        grps = get_groups(uid)
        posts = get_posts(uid)
        u_copy = dict(u)
        u_copy["accounts_count"] = len(accs)
        u_copy["groups_count"] = len(grps)
        u_copy["posts_count"] = len(posts)
        enriched.append(u_copy)
    return enriched

@app.get("/api/admin/stats")
async def api_admin_stats(admin: Dict[str, Any] = Depends(get_admin_user)):
    users = get_all_users_for_admin()
    active_users = [u for u in users if u.get("status") == "active"]
    pending_users = [u for u in users if u.get("status") == "pending"]
    expired_users = [u for u in users if u.get("status") == "expired"]

    # MRR from active non-admin users
    mrr = sum(u.get("subscription_price", 0) for u in active_users if u.get("role") != "admin")

    total_accounts = 0
    for u in users:
        total_accounts += len(get_accounts(u["id"]))

    return {
        "total_users": len(users),
        "active_users": len(active_users),
        "pending_users": len(pending_users),
        "expired_users": len(expired_users),
        "monthly_revenue": mrr,
        "total_accounts": total_accounts
    }

class AdminApproveRequest(BaseModel):
    plan_tier: Optional[str] = "tier_1"
    duration_days: Optional[int] = 30

@app.post("/api/admin/users/{user_id}/approve")
async def api_admin_approve(user_id: str, req: AdminApproveRequest, admin: Dict[str, Any] = Depends(get_admin_user)):
    try:
        updated = admin_approve_user(user_id, req.plan_tier or "tier_1", req.duration_days or 30)
        add_log("SUCCESS", f"Super Admin approved user @{updated['username']} for {updated['plan_info']['name']} ({req.duration_days} days active)", user_id="admin")
        return {"status": "success", "user": updated}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

class AdminExtendRequest(BaseModel):
    extra_days: Optional[int] = 30

@app.post("/api/admin/users/{user_id}/extend")
async def api_admin_extend(user_id: str, req: AdminExtendRequest, admin: Dict[str, Any] = Depends(get_admin_user)):
    try:
        updated = admin_extend_subscription(user_id, req.extra_days or 30)
        add_log("SUCCESS", f"Super Admin extended subscription for @{updated['username']} by {req.extra_days} days", user_id="admin")
        return {"status": "success", "user": updated}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

class AdminPlanRequest(BaseModel):
    plan_tier: str

@app.post("/api/admin/users/{user_id}/plan")
async def api_admin_plan(user_id: str, req: AdminPlanRequest, admin: Dict[str, Any] = Depends(get_admin_user)):
    try:
        updated = admin_update_user_plan(user_id, req.plan_tier)
        add_log("INFO", f"Super Admin changed user @{updated['username']} plan to {updated['plan_info']['name']}", user_id="admin")
        return {"status": "success", "user": updated}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

class AdminStatusRequest(BaseModel):
    status: str

@app.post("/api/admin/users/{user_id}/status")
async def api_admin_status(user_id: str, req: AdminStatusRequest, admin: Dict[str, Any] = Depends(get_admin_user)):
    try:
        updated = admin_set_user_status(user_id, req.status)
        add_log("INFO", f"Super Admin set user @{updated['username']} status to {req.status}", user_id="admin")
        return {"status": "success", "user": updated}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/admin/users/{user_id}")
async def api_admin_delete(user_id: str, admin: Dict[str, Any] = Depends(get_admin_user)):
    res = admin_delete_user(user_id)
    if not res:
        raise HTTPException(status_code=404, detail="User not found or cannot delete Super Admin")
    add_log("WARNING", f"Super Admin deleted user {user_id}", user_id="admin")
    return {"status": "success", "message": "User deleted."}

class EmailConfigModel(BaseModel):
    enabled: bool = True
    smtp_host: str = "smtp.hostinger.com"
    smtp_port: int = 465
    smtp_user: str = "noreply@omeglevideochat.online"
    smtp_password: Optional[str] = None
    smtp_use_ssl: bool = True
    smtp_use_tls: bool = False
    sender_email: str = "noreply@omeglevideochat.online"
    sender_name: str = "Telegram AutoPost Pro"

@app.get("/api/admin/email_config")
async def api_get_email_config(admin: Dict[str, Any] = Depends(get_admin_user)):
    cfg = load_email_config()
    cfg_safe = cfg.copy()
    if cfg_safe.get("smtp_password"):
        cfg_safe["smtp_password_set"] = True
        cfg_safe["smtp_password"] = "••••••••"
    else:
        cfg_safe["smtp_password_set"] = False
    return cfg_safe

@app.post("/api/admin/email_config")
async def api_save_email_config(req: EmailConfigModel, admin: Dict[str, Any] = Depends(get_admin_user)):
    current = load_email_config()
    cfg_to_save = req.dict()
    if not cfg_to_save.get("smtp_password") or cfg_to_save["smtp_password"] == "••••••••":
        cfg_to_save["smtp_password"] = current.get("smtp_password", "")
    elif cfg_to_save.get("smtp_password"):
        cfg_to_save["smtp_password"] = cfg_to_save["smtp_password"].replace(" ", "").strip()
    save_email_config(cfg_to_save)
    add_log("INFO", "Admin updated SMTP email configuration.", user_id="admin")
    return {"status": "success", "message": "Email settings saved successfully."}

@app.post("/api/admin/test_email")
async def api_test_email(admin: Dict[str, Any] = Depends(get_admin_user)):
    admin_user = get_user_by_id(admin["id"])
    admin_email = (admin_user.get("email") if admin_user else None) or admin.get("email") or "simardeep647.ss@gmail.com"
    test_otp = generate_otp()
    success, msg = dispatch_otp_email(admin_email, test_otp, "test")
    if success:
        return {"status": "success", "message": f"Test verification email sent to {admin_email} successfully! Please check your inbox and spam folder."}
    raise HTTPException(status_code=500, detail=f"Failed to send email: {msg}")

class AdminPasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str
    otp: str

@app.post("/api/admin/password/request_otp")
async def api_admin_password_request_otp(admin: Dict[str, Any] = Depends(get_admin_user)):
    admin_user = get_user_by_id(admin["id"])
    admin_email = admin_user.get("email") if admin_user else admin.get("email")
    if not admin_email:
        raise HTTPException(status_code=400, detail="No admin email configured.")

    otp = generate_otp()
    store_otp("admin_pwd_change", otp, "pwd_change", {"user_id": admin["id"]})
    success, msg = dispatch_otp_email(admin_email, otp, "login")
    add_log("WARNING", f"Password change OTP dispatched to admin email: {mask_email(admin_email)}", user_id="admin")
    return {
        "status": "otp_sent",
        "masked_email": mask_email(admin_email),
        "message": f"Security verification code sent to your admin email ({mask_email(admin_email)})."
    }

@app.post("/api/admin/password/verify_and_update")
async def api_admin_password_verify_and_update(
    req: AdminPasswordChangeRequest,
    admin: Dict[str, Any] = Depends(get_admin_user)
):
    try:
        is_valid, payload, err = verify_otp("admin_pwd_change", req.otp, "pwd_change")
        if not is_valid:
            raise HTTPException(status_code=400, detail=err or "Invalid or expired verification code.")

        admin_user = get_user_by_id(admin["id"])
        if not admin_user or not verify_password(req.current_password, admin_user["password_hash"], admin_user["salt"]):
            raise HTTPException(status_code=400, detail="Current password is incorrect.")

        if len(req.new_password) < 6:
            raise HTTPException(status_code=400, detail="New password must be at least 6 characters.")

        admin_change_password(admin["id"], req.new_password)
        add_log("WARNING", "Super Admin password updated successfully with email OTP verification.", user_id="admin")
        return {
            "status": "success",
            "message": "Admin password changed successfully! Please use your new password next time you sign in."
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ----------------- USER PORTAL: SCHEDULER & STATUS -----------------

@app.get("/api/status")
async def api_status(current_user: Dict[str, Any] = Depends(get_current_user)):
    uid = current_user["id"]
    status = get_scheduler_status(uid)
    accounts = get_accounts(uid)
    active_accounts = [a for a in accounts if a.get("is_active", True)]
    groups = get_groups(uid)
    all_posts = get_posts(uid)
    active_posts = get_active_posts(uid)

    return {
        **status,
        "user": sanitize_user(current_user),
        "total_accounts": len(accounts),
        "active_accounts": len(active_accounts),
        "total_groups": len(groups),
        "total_posts": len(all_posts),
        "active_posts": len(active_posts),
        "posts": all_posts
    }

@app.get("/api/config")
async def api_get_config(current_user: Dict[str, Any] = Depends(get_current_user)):
    return load_config(current_user["id"])

class ConfigUpdate(BaseModel):
    interval_value: float
    interval_unit: str = "hours"
    random_delay_min: int = 10
    random_delay_max: int = 25
    auto_join_groups: bool = True
    account_rotation: str = "round_robin"
    selected_account: Any = "all"
    selected_posts: Any = "all"
    post_rotation_mode: str = "random"
    post_selection_scope: str = "per_group"

@app.post("/api/config")
async def api_save_config(cfg: ConfigUpdate, current_user: Dict[str, Any] = Depends(get_current_user)):
    uid = current_user["id"]
    current = load_config(uid)
    current.update(cfg.model_dump())
    save_config(current, uid)
    add_log("INFO", f"Config updated: Interval {cfg.interval_value} {cfg.interval_unit}, Delay {cfg.random_delay_min}-{cfg.random_delay_max}s, Post Rotation: {cfg.post_rotation_mode.upper()}", user_id=uid)
    return {"status": "success", "config": current}

@app.post("/api/scheduler/start")
async def api_start_scheduler(current_user: Dict[str, Any] = Depends(get_current_user)):
    return start_scheduler(current_user["id"])

@app.post("/api/scheduler/stop")
async def api_stop_scheduler(current_user: Dict[str, Any] = Depends(get_current_user)):
    return stop_scheduler(current_user["id"])

@app.post("/api/scheduler/post_now")
async def api_post_now(current_user: Dict[str, Any] = Depends(get_current_user)):
    uid = current_user["id"]
    if current_user.get("role") != "admin" and current_user.get("is_locked"):
        raise HTTPException(status_code=403, detail="Subscription expired or locked. Please renew to broadcast.")
    asyncio.create_task(trigger_now(uid))
    return {"status": "triggered", "message": "Broadcast triggered in background."}

# ----------------- USER PORTAL: ACCOUNTS -----------------

@app.get("/api/accounts")
async def api_get_accounts(current_user: Dict[str, Any] = Depends(get_current_user)):
    accounts = get_accounts(current_user["id"])
    safe_accounts = []
    for a in accounts:
        ac = dict(a)
        if "api_hash" in ac and len(ac["api_hash"]) > 6:
            ac["api_hash_masked"] = ac["api_hash"][:3] + "..." + ac["api_hash"][-3:]
        safe_accounts.append(ac)
    return safe_accounts

class RequestCodeBody(BaseModel):
    api_id: int
    api_hash: str
    phone: str

@app.post("/api/accounts/request_code")
async def api_request_code(body: RequestCodeBody, current_user: Dict[str, Any] = Depends(get_current_user)):
    res = await start_login(body.api_id, body.api_hash, body.phone, owner_user_id=current_user["id"])
    return res

class SubmitCodeBody(BaseModel):
    phone: str
    code: str
    password: Optional[str] = None

@app.post("/api/accounts/submit_code")
async def api_submit_code(body: SubmitCodeBody, current_user: Dict[str, Any] = Depends(get_current_user)):
    res = await submit_login_code(body.phone, body.code, body.password, owner_user_id=current_user["id"])
    return res

class ResendCodeBody(BaseModel):
    phone: str

@app.post("/api/accounts/resend_code")
async def api_resend_code(body: ResendCodeBody, current_user: Dict[str, Any] = Depends(get_current_user)):
    res = await resend_code_sms(body.phone, owner_user_id=current_user["id"])
    return res

class StringSessionBody(BaseModel):
    api_id: int
    api_hash: str
    session_string: str

@app.post("/api/accounts/login_string")
async def api_login_string(body: StringSessionBody, current_user: Dict[str, Any] = Depends(get_current_user)):
    res = await login_with_string_session(body.api_id, body.api_hash, body.session_string, owner_user_id=current_user["id"])
    return res

@app.post("/api/accounts/{account_id}/toggle")
async def api_toggle_account(account_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    res = toggle_account_active(account_id, user_id=current_user["id"])
    if res is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return {"status": "success", "is_active": res}

@app.delete("/api/accounts/{account_id}")
async def api_delete_account(account_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    res = delete_account(account_id, user_id=current_user["id"])
    if not res:
        raise HTTPException(status_code=404, detail="Account not found")
    return {"status": "success", "message": "Account removed"}

@app.get("/api/accounts/{account_id}/fetch_groups")
async def api_fetch_account_groups(account_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    return await fetch_account_groups(account_id, user_id=current_user["id"])

class ImportAccountGroupsBody(BaseModel):
    targets: Optional[List[str]] = None

@app.post("/api/accounts/{account_id}/import_groups")
async def api_import_account_groups(account_id: str, body: ImportAccountGroupsBody, current_user: Dict[str, Any] = Depends(get_current_user)):
    return await import_account_groups(account_id, body.targets, user_id=current_user["id"])

# ----------------- USER PORTAL: GROUPS -----------------

@app.get("/api/groups")
async def api_get_groups(current_user: Dict[str, Any] = Depends(get_current_user)):
    return get_groups(user_id=current_user["id"])

class GroupsImportBody(BaseModel):
    raw_text: str

@app.post("/api/groups")
async def api_import_groups(body: GroupsImportBody, current_user: Dict[str, Any] = Depends(get_current_user)):
    uid = current_user["id"]
    added = import_groups_from_text(body.raw_text, user_id=uid)
    add_log("INFO", f"Imported {added} new target groups. Total active: {len(get_groups(uid))}", user_id=uid)
    return {"status": "success", "added": added, "total": len(get_groups(uid))}

@app.delete("/api/groups")
async def api_clear_groups(current_user: Dict[str, Any] = Depends(get_current_user)):
    uid = current_user["id"]
    clear_all_groups(user_id=uid)
    add_log("INFO", "Target groups cleared.", user_id=uid)
    return {"status": "success", "message": "All groups cleared."}

# ----------------- USER PORTAL: POSTS & CAMPAIGNS -----------------

@app.get("/api/posts")
async def api_get_posts(current_user: Dict[str, Any] = Depends(get_current_user)):
    return get_posts(user_id=current_user["id"])

@app.get("/api/posts/{post_id}")
async def api_get_single_post(post_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    post = get_post_by_id(post_id, user_id=current_user["id"])
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return post

class PostUpsertBody(BaseModel):
    id: Optional[str] = None
    title: Optional[str] = None
    html_content: str
    media_path: Optional[str] = None
    media_filename: Optional[str] = None
    media_url: Optional[str] = None
    media_name: Optional[str] = None
    has_media: bool = False
    is_active: bool = True
    assigned_account: Optional[Any] = "all"

@app.post("/api/posts")
async def api_save_post(body: PostUpsertBody, current_user: Dict[str, Any] = Depends(get_current_user)):
    uid = current_user["id"]
    _, entities = parse_html_to_message_and_entities(body.html_content)
    emojis = extract_custom_emojis(entities)
    data = body.model_dump()
    data["custom_emojis_count"] = len(emojis)
    data["custom_emojis"] = emojis
    saved = upsert_post(data, user_id=uid)
    add_log("INFO", f"Saved campaign post: '{saved.get('title', saved.get('id'))}' (3D Emojis: {len(emojis)})", user_id=uid)
    return {"status": "success", "post": saved}

@app.post("/api/posts/{post_id}/toggle")
async def api_toggle_post(post_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    res = toggle_post_active(post_id, user_id=current_user["id"])
    if res is None:
        raise HTTPException(status_code=404, detail="Post not found")
    return {"status": "success", "is_active": res}

@app.delete("/api/posts/{post_id}")
async def api_delete_post(post_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    uid = current_user["id"]
    res = delete_post(post_id, user_id=uid)
    if not res:
        raise HTTPException(status_code=404, detail="Post not found")
    add_log("INFO", f"Deleted campaign post {post_id}.", user_id=uid)
    return {"status": "success", "message": "Post removed"}

@app.post("/api/posts/{post_id}/upload_media")
async def api_upload_post_media(post_id: str, file: UploadFile = File(...), current_user: Dict[str, Any] = Depends(get_current_user)):
    uid = current_user["id"]
    post = get_post_by_id(post_id, user_id=uid)
    if not post:
        post = {"id": post_id, "title": f"Post {post_id}", "html_content": ""}

    # Sanitize upload filename
    orig_name = os.path.basename(file.filename or "media.jpg").replace(" ", "_")
    clean_name = re.sub(r'[^a-zA-Z0-9_\.-]', '', orig_name) or "media.jpg"

    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    disk_filename = f"post_{uid}_{post_id}_{int(time.time())}_{clean_name}"
    file_path = MEDIA_DIR / disk_filename

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    post["media_path"] = str(file_path)
    post["media_filename"] = disk_filename
    post["media_url"] = f"/media/{disk_filename}"
    post["media_name"] = file.filename
    post["has_media"] = True

    saved = upsert_post(post, user_id=uid)
    add_log("INFO", f"Attached media '{file.filename}' to {post.get('title')}.", user_id=uid)
    return {"status": "success", "post": saved}

@app.delete("/api/posts/{post_id}/media")
async def api_remove_post_media(post_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    uid = current_user["id"]
    post = get_post_by_id(post_id, user_id=uid)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    try:
        resolved = resolve_post_media_path(post)
        if resolved and os.path.isfile(resolved):
            os.remove(resolved)
    except Exception:
        pass

    post["media_path"] = None
    post["media_filename"] = None
    post["media_url"] = None
    post["media_name"] = None
    post["has_media"] = False

    saved = upsert_post(post, user_id=uid)
    add_log("INFO", f"Removed media from {post.get('title')}.", user_id=uid)
    return {"status": "success", "post": saved}

# Backward compatibility routes
@app.get("/api/draft")
async def api_get_draft(current_user: Dict[str, Any] = Depends(get_current_user)):
    return get_draft(user_id=current_user["id"])

class DraftSaveBody(BaseModel):
    html_content: str

@app.post("/api/draft")
async def api_save_draft(body: DraftSaveBody, current_user: Dict[str, Any] = Depends(get_current_user)):
    uid = current_user["id"]
    draft = get_draft(user_id=uid)
    draft["html_content"] = body.html_content
    _, entities = parse_html_to_message_and_entities(body.html_content)
    emojis = extract_custom_emojis(entities)
    draft["custom_emojis_count"] = len(emojis)
    draft["custom_emojis"] = emojis
    saved = upsert_post(draft, user_id=uid)
    return {"status": "success", "draft": saved}

class FetchPostRequest(BaseModel):
    source_chat: str
    message_id: Optional[int] = None
    target_post_id: Optional[str] = None

@app.post("/api/draft/fetch_telegram")
@app.post("/api/posts/fetch_telegram")
async def api_fetch_telegram_post(req: FetchPostRequest, current_user: Dict[str, Any] = Depends(get_current_user)):
    res = await fetch_telegram_post(req.source_chat, req.message_id, req.target_post_id, user_id=current_user["id"])
    return res

# ----------------- USER PORTAL: LOGS & STREAMING -----------------

@app.get("/api/logs")
async def api_get_logs(limit: int = 100, current_user: Dict[str, Any] = Depends(get_current_user)):
    if current_user.get("role") == "admin":
        return get_logs(limit, user_id=None)
    return get_logs(limit, user_id=current_user["id"])

@app.get("/api/logs/stream")
async def api_stream_logs(request: Request):
    # Support token in cookie, header, or query param
    token = request.query_params.get("token") or request.cookies.get("auth_token")
    if not token and "Authorization" in request.headers:
        auth_hdr = request.headers["Authorization"]
        if auth_hdr.startswith("Bearer "):
            token = auth_hdr[7:].strip()

    user = authenticate_token(token) if token else None
    user_id = user["id"] if user else None
    role = user["role"] if user else "user"

    async def event_generator():
        q = subscribe_logs(user_id=user_id, role=role)
        try:
            # Yield recent logs first
            recent = get_logs(30, user_id=user_id)
            for item in recent:
                yield f"data: {json.dumps(item)}\n\n"

            while True:
                if await request.is_disconnected():
                    break
                try:
                    entry = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {json.dumps(entry)}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            unsubscribe_logs(q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

# ----------------- ROOT DASHBOARD -----------------

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        with open(index_file, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h1>Telegram Automation Hub</h1><p>Static files loading...</p>")
