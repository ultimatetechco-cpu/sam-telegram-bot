# ✈️ Telegram AutoPost Pro — 3D Animated Emoji Automation

A professional, high-performance Telegram group broadcast automation system featuring multi-account rotation, dynamic interval scheduling (e.g., every 2 hours, 3 hours, 4 hours, or custom), anti-flood human delay simulations, and full native support for **Telegram 3D animated custom emojis** and media attachments.

---

## 🌟 Key Features

1. **Flexible Interval Scheduler**:
   - One-click presets: `30 Min`, `1 Hour`, `2 Hours`, `3 Hours`, `4 Hours`, `6 Hours`, `12 Hours`, `24 Hours`.
   - Custom intervals (specify any value in minutes or hours).
   - Live countdown display: Shows exact time remaining until the next scheduled broadcast.
   - Immediate **"Post Now"** button to execute a broadcast cycle on demand.

2. **Full Support for 3D Animated Custom Emojis**:
   - **Method 1 (Recommended)**: Draft your post natively inside Telegram (in your **Saved Messages** or a private channel) using Telegram's built-in 3D animated emoji picker. The dashboard can fetch your draft with 1-click, preserving 100% of the custom emoji document IDs and animations losslessly!
   - **Method 2**: Write directly in the dashboard using standard HTML or custom emoji tags: `<tg-emoji emoji-id="5368324170671202286">🔥</tg-emoji>`.
   - Live interactive Telegram message preview bubble.

3. **Multi-Account Rotation (API ID & API Hash)**:
   - Add multiple Telegram user accounts using `api_id`, `api_hash`, and phone numbers.
   - In-app OTP verification flow with 2FA password support.
   - Rotates through accounts (Round-Robin or Random) to prevent Telegram rate limits and flood bans.
   - Visual detection of **⭐ Telegram Premium** status per account.

4. **Auto-Fetch Groups from Telegram Account**:
   - **1-Click Dialog Scanner**: Directly scans all joined groups & supergroups from your connected Telegram account.
   - Preview group names, `@usernames`, member counts, and supergroup tags.
   - Select All or choose specific groups to import directly into your target list without manual copy-pasting.

5. **Multi-Post Pool & Random Anti-Ban Rotation**:
   - **Campaign Pool**: Create Post #1, Post #2, Post #3, Post #4, etc.
   - Each post can have its own text, native 3D animated emojis, and media attachment.
   - **🎲 Random Rotation Mode**: Picks a different post randomly from the active pool on every group dispatch. Posting varied content protects your accounts against Telegram spam pattern detection!
   - **🔄 Sequential Mode**: Post 1 ➔ Post 2 ➔ Post 3...
   - Toggle individual posts on or off in the rotation.

6. **Anti-Flood & Anti-Ban Protection**:
   - Configurable randomized human delay between each group post (e.g., 10 to 25 seconds).
   - Automatic handling of Telegram `FloodWaitError` cooldowns without crashing.

7. **Modern Dark Web Dashboard & Real-Time Console**:
   - Modern browser dashboard running locally on `http://localhost:8080`.
   - Real-time Server-Sent Events (SSE) live console logs.

---

## 🚀 Quick Start Guide

### 1. Requirements & Installation
The dependencies are already installed in your Python environment:
- Python 3.10+
- Telethon
- FastAPI
- Uvicorn
- aiohttp
- python-multipart

If needed, you can reinstall anytime via:
```bash
pip install -r requirements.txt
```

### 2. Launch the Application
Simply double-click `start.bat` or run in terminal:
```bash
python main.py
```
Open your browser and navigate to:
👉 **[http://localhost:8080](http://localhost:8080)**

---

## 📱 How to Obtain Telegram API Credentials (`api_id` & `api_hash`)

To connect any Telegram user account:
1. Go to [https://my.telegram.org](https://my.telegram.org) and log in with your Telegram phone number.
2. Click **API development tools**.
3. Enter an app title (e.g. `MyAutoPost`) and short name.
4. Telegram will generate your **App api_id** (number) and **App api_hash** (string).
5. In the AutoPost Web Dashboard:
   - Go to the **Telegram Accounts** tab.
   - Click **Connect Telegram Account**.
   - Enter your phone number, API ID, and API Hash.
   - Click **Request Login Code**, enter the OTP code received in your Telegram app, and click **Verify & Connect**!

---

## 🎨 How to Post with 3D Animated Emojis

Telegram's custom animated 3D emojis are tied to 64-bit Telegram document IDs. The easiest and most reliable way to post them is:

### Method 1: Draft in Telegram & Fetch (Recommended)
1. Open your Telegram app.
2. Go to your **Saved Messages** (or create a private draft channel).
3. Type your post message and use your Telegram keyboard to pick any **3D animated emojis**, bold/italic text, links, or photos/videos.
4. Send the message to your Saved Messages.
5. In the Web Dashboard:
   - Go to **Post Studio & 3D Emojis**.
   - In the **Source Chat** box, enter `me` (for Saved Messages) or `@your_channel`.
   - Click **📥 Fetch Post & 3D Emojis**.
   - Your post is now loaded with all native 3D animated emojis intact!

### Method 2: Manual HTML Custom Emoji Tag
You can also manually insert custom emoji tags in the editor:
```html
🔥 <tg-emoji emoji-id="5368324170671202286">🔥</tg-emoji> <b>Mega Announcement!</b>
Join our channel: <a href="https://t.me/yourchannel">Click here</a>
```

> **Note on Telegram Custom Emojis**: To broadcast custom animated emojis that render natively in any group, the sending Telegram account should have **Telegram Premium** (or the target group must permit custom emojis for all members).

---

## ⏱️ Setting Up Posting Intervals & Scheduling

1. Go to the **Schedule & Intervals** tab.
2. Select a quick preset: **2 Hours**, **3 Hours**, **4 Hours**, etc.
3. (Optional) Adjust the **Anti-flood delay** (default: 10s - 25s between group posts).
4. Click **Start Automation**.
5. The dashboard will show a live countdown timer and start broadcasting your post to all target groups at your selected intervals!

---

## 📁 File Structure

```
d:\Sam Telegram\
├── app/
│   ├── config.py            # App settings, paths, interval configurations
│   ├── storage.py           # Persistent JSON storage for accounts, groups, drafts, logs
│   ├── emoji_parser.py      # Custom emoji & HTML parser (<tg-emoji> support)
│   ├── telegram_mgr.py      # Telethon client manager & OTP login engine
│   ├── poster_engine.py     # Multi-account rotation & group posting dispatcher
│   ├── scheduler_service.py # Background interval runner & countdown timer
│   └── web/
│       ├── api.py           # FastAPI REST endpoints & live SSE log stream
│       └── static/
│           ├── index.html   # Modern responsive dashboard UI
│           └── app.js       # Dynamic frontend logic & polling
├── data/                    # JSON data stores & media uploads
├── sessions/                # Telethon SQLite session files
├── main.py                  # Server launch entry point
├── start.bat                # 1-Click Windows launcher
└── requirements.txt         # Project dependencies
```
