import os
import json
import time
import secrets
import smtplib
import ssl
import urllib.request
import urllib.error
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import Dict, Any, Optional

from app.config import DATA_DIR, ensure_directories

EMAIL_CONFIG_FILE = DATA_DIR / "email_config.json"

DEFAULT_EMAIL_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "provider": "gmail",
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 465,
    "smtp_user": "simardeep647.ss@gmail.com",
    "smtp_password": "",
    "smtp_use_ssl": True,
    "smtp_use_tls": False,
    "sender_email": "simardeep647.ss@gmail.com",
    "sender_name": "Telegram AutoPost Pro",
    "hostinger_php_mail_enabled": True
}

# In-memory storage for active OTPs (10-minute expiry)
# Format: { "key": { "otp": "123456", "expires_at": float, "attempts": int, "payload": dict } }
PENDING_OTPS: Dict[str, Dict[str, Any]] = {}
OTP_EXPIRY_SECONDS = 600  # 10 minutes

def load_email_config() -> Dict[str, Any]:
    ensure_directories()
    if EMAIL_CONFIG_FILE.exists():
        try:
            with open(EMAIL_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {**DEFAULT_EMAIL_CONFIG, **data}
        except Exception:
            pass
    return DEFAULT_EMAIL_CONFIG.copy()

def save_email_config(cfg: Dict[str, Any]) -> None:
    ensure_directories()
    with open(EMAIL_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

def mask_email(email: str) -> str:
    """Masks an email for safe public display, e.g. ultimatetechco@gmail.com -> u***o@gmail.com"""
    if not email or "@" not in email:
        return email
    user_part, domain_part = email.split("@", 1)
    if len(user_part) <= 2:
        masked_user = user_part[0] + "***"
    else:
        masked_user = user_part[0] + "***" + user_part[-1]
    return f"{masked_user}@{domain_part}"

def generate_otp() -> str:
    """Generates a secure 6-digit numeric OTP."""
    return f"{secrets.randbelow(900000) + 100000}"

def store_otp(key: str, otp: str, purpose: str, payload: Optional[Dict[str, Any]] = None) -> None:
    """Stores a pending OTP in memory with 10-minute validity."""
    now = time.time()
    clean_key = key.lower().strip()
    PENDING_OTPS[clean_key] = {
        "otp": otp,
        "purpose": purpose,
        "payload": payload or {},
        "created_at": now,
        "expires_at": now + OTP_EXPIRY_SECONDS,
        "attempts": 0
    }

def verify_otp(key: str, user_otp: str, purpose: str) -> (bool, Optional[Dict[str, Any]], str):
    """
    Verifies a user-supplied OTP.
    Returns: (is_valid, payload, error_message)
    """
    clean_key = key.lower().strip()
    entry = PENDING_OTPS.get(clean_key)
    if not entry:
        return False, None, "No active verification code found or it has expired. Please request a new code."

    if time.time() > entry["expires_at"]:
        PENDING_OTPS.pop(clean_key, None)
        return False, None, "Verification code has expired (valid for 10 minutes). Please request a new code."

    if entry.get("purpose") != purpose:
        return False, None, "Invalid verification context."

    entry["attempts"] += 1
    if entry["attempts"] > 5:
        PENDING_OTPS.pop(clean_key, None)
        return False, None, "Too many failed attempts. For security, please request a new code."

    # Constant-time comparison for security
    if secrets.compare_digest(entry["otp"].strip(), user_otp.strip()):
        payload = entry.get("payload")
        PENDING_OTPS.pop(clean_key, None)
        return True, payload, ""

    remaining = 5 - entry["attempts"]
    return False, None, f"Incorrect verification code. Please check your email. ({remaining} attempts remaining)"

def build_otp_html(otp_code: str, purpose: str = "registration") -> str:
    purpose_title = "Account Registration" if purpose == "registration" else "Portal Security Login"
    purpose_desc = (
        "Thank you for registering on Telegram AutoPost Pro. Please use the verification code below to complete your registration."
        if purpose == "registration"
        else "A sign-in request was initiated for your Telegram AutoPost Pro tenant portal. Please enter the security code below to access your account."
    )

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Verification Code</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
      background-color: #090d16;
      margin: 0;
      padding: 30px 15px;
      color: #f1f5f9;
    }}
    .wrapper {{
      max-width: 520px;
      margin: 0 auto;
      background: linear-gradient(180deg, #131b2e 0%, #0f172a 100%);
      border: 1px solid #1e293b;
      border-radius: 20px;
      padding: 36px 30px;
      box-shadow: 0 20px 40px rgba(0,0,0,0.4);
    }}
    .brand {{
      text-align: center;
      margin-bottom: 24px;
    }}
    .brand-icon {{
      display: inline-block;
      width: 52px;
      height: 52px;
      border-radius: 14px;
      background: linear-gradient(135deg, #2563eb 0%, #06b6d4 100%);
      color: #ffffff;
      line-height: 52px;
      font-size: 26px;
      box-shadow: 0 8px 16px rgba(37,99,235,0.3);
    }}
    .brand-title {{
      font-size: 20px;
      font-weight: 700;
      color: #ffffff;
      margin-top: 12px;
      letter-spacing: -0.5px;
    }}
    .badge {{
      display: inline-block;
      padding: 4px 12px;
      background-color: rgba(37,99,235,0.15);
      border: 1px solid rgba(37,99,235,0.3);
      color: #60a5fa;
      border-radius: 20px;
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin-top: 6px;
    }}
    .content-box {{
      text-align: center;
      margin: 24px 0;
    }}
    .desc {{
      font-size: 13px;
      line-height: 1.6;
      color: #94a3b8;
      margin-bottom: 24px;
    }}
    .otp-card {{
      background: #090e1a;
      border: 2px dashed #2563eb;
      border-radius: 14px;
      padding: 20px;
      margin: 0 auto 24px auto;
      max-width: 340px;
    }}
    .otp-label {{
      font-size: 11px;
      font-weight: 600;
      color: #64748b;
      text-transform: uppercase;
      letter-spacing: 1px;
      margin-bottom: 8px;
    }}
    .otp-digits {{
      font-size: 36px;
      font-weight: 800;
      letter-spacing: 8px;
      color: #38bdf8;
      font-family: 'Courier New', Courier, monospace;
      margin: 4px 0;
    }}
    .expiry {{
      font-size: 11px;
      color: #eab308;
      margin-top: 6px;
    }}
    .warning {{
      background-color: rgba(239, 68, 68, 0.08);
      border: 1px solid rgba(239, 68, 68, 0.2);
      border-radius: 10px;
      padding: 12px 16px;
      font-size: 11px;
      color: #fca5a5;
      line-height: 1.5;
      text-align: left;
    }}
    .footer {{
      margin-top: 30px;
      padding-top: 20px;
      border-top: 1px solid #1e293b;
      text-align: center;
      font-size: 11px;
      color: #64748b;
    }}
  </style>
</head>
<body>
  <div class="wrapper">
    <div class="brand">
      <div class="brand-icon">&#9992;</div>
      <div class="brand-title">Telegram AutoPost Pro</div>
      <div class="badge">{purpose_title}</div>
    </div>
    
    <div class="content-box">
      <p class="desc">{purpose_desc}</p>
      
      <div class="otp-card">
        <div class="otp-label">Your Verification Code</div>
        <div class="otp-digits">{otp_code}</div>
        <div class="expiry">&#9200; Valid for 10 minutes</div>
      </div>
      
      <div class="warning">
        &#9888; <strong>Security Notice:</strong> Never share this code with anyone. Telegram AutoPost Pro administrators will never ask for your verification code.
      </div>
    </div>
    
    <div class="footer">
      This is an automated security message sent to your registered email.<br>
      &copy; Telegram AutoPost Pro &bull; Multi-Tenant Automation Hub
    </div>
  </div>
</body>
</html>"""

def send_email_via_smtp(to_email: str, subject: str, html_content: str, text_content: str) -> (bool, str):
    """Sends email via standard SMTP server."""
    cfg = load_email_config()
    host = cfg.get("smtp_host", "").strip()
    port = int(cfg.get("smtp_port", 465))
    user = cfg.get("smtp_user", "").strip()
    password = cfg.get("smtp_password", "").strip()
    # Strip spaces for Google App Passwords (e.g. "abcd efgh ijkl mnop" -> "abcdefghijklmnop")
    if password:
        password = password.replace(" ", "")
        
    sender_email = cfg.get("sender_email", user) or user
    sender_name = cfg.get("sender_name", "Telegram AutoPost Pro")
    use_ssl = cfg.get("smtp_use_ssl", True)
    use_tls = cfg.get("smtp_use_tls", False)

    if not host:
        return False, "SMTP Host is not configured."

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{sender_name} <{sender_email}>"
    msg["To"] = to_email

    msg.attach(MIMEText(text_content, "plain", "utf-8"))
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    try:
        if use_ssl or port == 465:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            with smtplib.SMTP_SSL(host, port, context=context, timeout=15) as server:
                if user and password:
                    server.login(user, password)
                server.sendmail(sender_email, [to_email], msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=15) as server:
                if use_tls or port == 587:
                    context = ssl.create_default_context()
                    context.check_hostname = False
                    context.verify_mode = ssl.CERT_NONE
                    server.starttls(context=context)
                if user and password:
                    server.login(user, password)
                server.sendmail(sender_email, [to_email], msg.as_string())
        return True, "Email sent successfully via SMTP."
    except Exception as e:
        return False, str(e)

def send_via_hostinger_mail_bridge(to_email: str, subject: str, html_content: str) -> (bool, str):
    """Fallback method using Hostinger's PHP mail() bridge via built-in urllib."""
    try:
        url = "https://omeglevideochat.online/telegrambot/api_proxy.php?action=send_mail"
        payload = json.dumps({"to": to_email, "subject": subject, "html": html_content}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "TelegramAutoPost/2.0"},
            method="POST"
        )
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=10, context=ctx) as response:
            status = response.getcode()
            if status == 200:
                return True, "Email sent via Hostinger mail gateway."
            return False, f"Hostinger gateway returned status {status}"
    except Exception as e:
        return False, f"Hostinger gateway request failed: {e}"

def dispatch_otp_email(to_email: str, otp_code: str, purpose: str = "registration") -> (bool, str):
    """
    Main entry point for dispatching OTP code to user's email.
    Subject and body are rendered, then dispatched via configured SMTP or Hostinger mail.
    """
    subject = f"{otp_code} is your Telegram AutoPost Pro Verification Code"
    plain_text = (
        f"Your Telegram AutoPost Pro Verification Code is: {otp_code}\n\n"
        f"This code is valid for 10 minutes.\n"
        f"For security reasons, do not share this code with anyone.\n"
    )
    html_content = build_otp_html(otp_code, purpose)

    # 1. ALWAYS log OTP to terminal stdout and system audit log for complete admin transparency
    print(f"\n" + "=" * 60, flush=True)
    print(f" >>> [SECURITY OTP CODE DISPATCH] <<<", flush=True)
    print(f" Recipient : {to_email}", flush=True)
    print(f" Purpose   : {purpose.upper()}", flush=True)
    print(f" OTP Code  : {otp_code} (Valid for 10 minutes)", flush=True)
    print("=" * 60 + "\n", flush=True)

    try:
        from app.storage import add_log
        add_log("WARNING", f"[SECURITY OTP] Code for {to_email} ({purpose}): {otp_code} (Valid 10 mins)", user_id="admin")
    except Exception:
        pass

    cfg = load_email_config()
    
    # 2. If SMTP password is provided, prioritize authentic direct SMTP delivery
    if cfg.get("smtp_password", "").strip():
        success, msg = send_email_via_smtp(to_email, subject, html_content, plain_text)
        if success:
            print(f"[+] Email successfully delivered via SMTP to {to_email}")
            return True, msg
        print(f"[!] SMTP sending failed: {msg}. Trying Hostinger fallback...")

    # 3. Fallback to Hostinger Mail Gateway
    success, msg = send_via_hostinger_mail_bridge(to_email, subject, html_content)
    if success:
        return True, msg

    # 4. If neither worked, try standard SMTP even if password is empty (e.g. local relay)
    return send_email_via_smtp(to_email, subject, html_content, plain_text)
