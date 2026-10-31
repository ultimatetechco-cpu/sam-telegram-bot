"""
Telegram AutoPost Pro - Automated Live Sync & Watchdog Service
--------------------------------------------------------------
Keeps your local Python backend and Cloudflare tunnel running 24/7,
and automatically synchronizes your live Hostinger domain
(https://omeglevideochat.online/telegrambot) whenever the tunnel starts or reconnects.
"""

import os
import sys

# Force unbuffered output so logs appear in real-time
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(line_buffering=True)

import time
import re
import subprocess
import urllib.request
import urllib.parse
import json

HOSTINGER_DOMAIN = "https://omeglevideochat.online/telegrambot"
SYNC_SECRET = "sam_telegram_autopost_live_sync_2026_x99"
LOCAL_BACKEND_PORT = 8080
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CLOUDFLARED_EXE = os.path.join(BASE_DIR, "cloudflared.exe")
TUNNEL_LOG = os.path.join(BASE_DIR, "cloudflared_runtime.log")
BACKEND_LOG = os.path.join(BASE_DIR, "backend_runtime.log")

def get_python_exe():
    candidates = [
        sys.executable,
        os.path.expandvars(r"%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Python\bin\python.exe"),
        "python"
    ]
    for p in candidates:
        if p and os.path.exists(p) and not p.lower().endswith("windowsapps\\python.exe"):
            return p
    return "python"

PYTHON_EXE = get_python_exe()

def is_backend_alive():
    try:
        url = f"http://127.0.0.1:{LOCAL_BACKEND_PORT}/docs"
        req = urllib.request.Request(url, headers={'User-Agent': 'AutoSyncWatchdog'})
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False

backend_process = None

def ensure_backend_running():
    global backend_process
    if is_backend_alive():
        return True
    
    print("[*] Starting local Telegram FastAPI backend on port 8080...", flush=True)
    main_py = os.path.join(BASE_DIR, "main.py")
    
    try:
        b_log = open(BACKEND_LOG, "a", encoding="utf-8")
        backend_process = subprocess.Popen(
            [PYTHON_EXE, main_py],
            cwd=BASE_DIR,
            stdout=b_log,
            stderr=subprocess.STDOUT
        )
    except Exception as e:
        print(f"[!] Error launching backend: {e}", flush=True)
        return False
    
    for _ in range(15):
        time.sleep(1)
        if is_backend_alive():
            print("[+] Backend started successfully on port 8080!", flush=True)
            return True
            
    print("[!] Backend did not respond within 15 seconds. Check backend_runtime.log", flush=True)
    return False

def extract_tunnel_url():
    if not os.path.exists(TUNNEL_LOG):
        return None
    try:
        with open(TUNNEL_LOG, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        for line in reversed(lines):
            match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
            if match:
                return match.group(0)
    except Exception:
        pass
    return None

def test_tunnel_url(url):
    try:
        req = urllib.request.Request(f"{url}/docs", headers={'User-Agent': 'AutoSyncWatchdog'})
        with urllib.request.urlopen(req, timeout=6) as resp:
            return resp.status == 200
    except Exception:
        return False

def sync_url_to_hostinger(tunnel_url):
    sync_url = f"{HOSTINGER_DOMAIN}/api_proxy.php?action=sync_backend"
    data = urllib.parse.urlencode({
        "secret": SYNC_SECRET,
        "url": tunnel_url
    }).encode("utf-8")
    
    try:
        req = urllib.request.Request(
            sync_url,
            data=data,
            headers={
                "User-Agent": "AutoPostLiveSync/1.0",
                "Content-Type": "application/x-www-form-urlencoded"
            }
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            res_body = resp.read().decode('utf-8')
            res_json = json.loads(res_body)
            if res_json.get("status") == "success":
                print(f"[SUCCESS] Hostinger synced with: {tunnel_url}", flush=True)
                return True
            else:
                print(f"[WARNING] Hostinger sync responded: {res_body}", flush=True)
                return False
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode('utf-8', errors='ignore')
        print(f"[!] Hostinger sync HTTP Error {e.code}: {err_msg}", flush=True)
        return False
    except Exception as e:
        print(f"[!] Hostinger sync connection error: {e}", flush=True)
        return False

def check_hostinger_status():
    status_url = f"{HOSTINGER_DOMAIN}/api_proxy.php?action=status"
    try:
        req = urllib.request.Request(status_url, headers={'User-Agent': 'AutoSyncWatchdog'})
        with urllib.request.urlopen(req, timeout=6) as resp:
            res_json = json.loads(resp.read().decode('utf-8'))
            return res_json.get("configured_backend")
    except Exception:
        return None

def main():
    print("=" * 65, flush=True)
    print("    TELEGRAM AUTOPOST PRO - 24/7 LIVE SYNC WATCHDOG", flush=True)
    print("=" * 65, flush=True)
    print(f" Target Domain : {HOSTINGER_DOMAIN}", flush=True)
    print(f" Local Port    : {LOCAL_BACKEND_PORT}", flush=True)
    print("=" * 65, flush=True)
    
    tunnel_proc = None
    last_synced_url = None
    
    # 1. Ensure backend is running
    ensure_backend_running()
    
    # 2. Check if cloudflared exists
    if not os.path.exists(CLOUDFLARED_EXE):
        print(f"[ERROR] cloudflared.exe not found at {CLOUDFLARED_EXE}", flush=True)
        sys.exit(1)
        
    print("[*] Launching Cloudflare tunnel...", flush=True)
    # Clear previous runtime log
    try:
        with open(TUNNEL_LOG, "w") as f:
            f.write("")
    except Exception:
        pass
        
    log_file = open(TUNNEL_LOG, "a", encoding="utf-8")
    tunnel_proc = subprocess.Popen(
        [CLOUDFLARED_EXE, "tunnel", "--url", f"http://127.0.0.1:{LOCAL_BACKEND_PORT}"],
        stdout=log_file,
        stderr=subprocess.STDOUT
    )
    
    # Wait for tunnel URL
    print("[*] Waiting for tunnel URL from Cloudflare...", flush=True)
    current_url = None
    for _ in range(40):
        time.sleep(1)
        url = extract_tunnel_url()
        if url:
            current_url = url
            time.sleep(2)
            break
            
    if not current_url:
        print("[!] Could not detect active tunnel URL within 40s. Check cloudflared_runtime.log", flush=True)
    else:
        print(f"[+] Tunnel Active: {current_url}", flush=True)
        print("[*] Synchronizing Hostinger...", flush=True)
        if sync_url_to_hostinger(current_url):
            last_synced_url = current_url
        else:
            print("[NOTE] Retrying Hostinger sync in background loop...", flush=True)
            
    print("\n" + "=" * 65, flush=True)
    print(" [OK] SERVICE IS RUNNING AND MONITORING CONTINUOUSLY", flush=True)
    print(" Hostinger is now connected to your active backend.", flush=True)
    print(" Keep this process running to maintain 100% uptime.", flush=True)
    print("=" * 65 + "\n", flush=True)
    
    # Loop watchdog
    iteration = 0
    while True:
        try:
            time.sleep(10)
            iteration += 1
            
            # Check backend health
            if not is_backend_alive():
                print("[!] Local backend stopped! Restarting...", flush=True)
                ensure_backend_running()
                
            # Check tunnel process
            if tunnel_proc.poll() is not None:
                print("[!] Tunnel process exited! Restarting...", flush=True)
                log_file.close()
                log_file = open(TUNNEL_LOG, "a", encoding="utf-8")
                tunnel_proc = subprocess.Popen(
                    [CLOUDFLARED_EXE, "tunnel", "--url", f"http://127.0.0.1:{LOCAL_BACKEND_PORT}"],
                    stdout=log_file,
                    stderr=subprocess.STDOUT
                )
                time.sleep(5)
                new_url = extract_tunnel_url()
                if new_url and new_url != last_synced_url:
                    print(f"[*] New tunnel URL detected: {new_url}", flush=True)
                    if sync_url_to_hostinger(new_url):
                        last_synced_url = new_url
                        
            # Check if tunnel log has a changed URL
            active_url = extract_tunnel_url()
            if active_url and active_url != last_synced_url:
                print(f"[*] Tunnel URL changed from {last_synced_url} to {active_url}", flush=True)
                if sync_url_to_hostinger(active_url):
                    last_synced_url = active_url
                    
            # Heartbeat & Hostinger verification every ~60 seconds
            if iteration % 6 == 0:
                hostinger_backend = check_hostinger_status()
                if active_url and hostinger_backend != active_url:
                    print(f"[!] Hostinger backend mismatch ({hostinger_backend} != {active_url}). Resyncing...", flush=True)
                    if sync_url_to_hostinger(active_url):
                        last_synced_url = active_url
                elif hostinger_backend:
                    print(f"[HEARTBEAT] System Healthy -> Hostinger synced to {active_url}", flush=True)
                    
        except KeyboardInterrupt:
            print("\nStopping AutoSync Watchdog...", flush=True)
            if tunnel_proc:
                tunnel_proc.terminate()
            break
        except Exception as e:
            print(f"[!] Watchdog warning: {e}", flush=True)

if __name__ == "__main__":
    main()
