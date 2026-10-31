import asyncio
import os
import sys
import uvicorn
from app.config import load_config, ensure_directories
from app.storage import add_log, get_draft, save_draft, DEFAULT_DRAFT
from app.auth_mgr import init_auth

import io
# Ensure Windows terminal doesn't crash on emoji prints
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'buffer'):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

def print_banner(host: str, port: int):
    print("=" * 65)
    print("      TELEGRAM AUTOPOST PRO - 3D ANIMATED EMOJI AUTOMATION      ")
    print("=" * 65)
    print(f"  * Web Dashboard URL : http://localhost:{port}")
    print(f"  * Network URL       : http://{host}:{port}")
    print("  * Multi-Account     : Telethon API ID / API Hash Support")
    print("  * 3D Emoji Engine   : Telegram Custom Animated Emojis Supported")
    print("  * Flexible Timing   : 30m, 1h, 2h, 3h, 4h, or Custom Intervals")
    print("=" * 65)
    print("  Press CTRL+C anytime in this terminal to stop the server.")
    print("=" * 65)

def main():
    ensure_directories()
    init_auth()
    config = load_config()
    
    # Ensure draft exists
    try:
        get_draft()
    except Exception:
        save_draft(DEFAULT_DRAFT)

    host = os.environ.get("HOST", config.get("web_host", "0.0.0.0"))
    port = int(os.environ.get("PORT", config.get("web_port", 8080)))

    print_banner(host, port)
    add_log("INFO", f"Starting Telegram AutoPost Pro Web Server on port {port}...")

    # Run Uvicorn server
    uvicorn.run(
        "app.web.api:app",
        host=host,
        port=port,
        reload=False,
        log_level="warning"
    )

if __name__ == "__main__":
    main()
