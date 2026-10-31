# 🚀 cPanel Deployment Guide - Telegram AutoPost Pro

This guide explains how to deploy your **Telegram AutoPost Pro SaaS Portal** to your custom domain on **cPanel**.

---

## 📦 Step 1: Prepare the Files for Upload

Zip your project folder. Make sure to include:
- `app/` (Contains all backend engines and frontend files)
- `data/` (Contains user database, accounts, groups, posts, config)
- `sessions/` (Directory for Telegram Telethon sessions)
- `requirements.txt`
- `passenger_wsgi.py` (Prepared for cPanel Passenger)
- `main.py`
- `.htaccess`

> ⚠️ **Note:** Do NOT include `cloudflared.exe` or `__pycache__` in your ZIP.

---

## 🌐 Method 1: cPanel "Setup Python App" (Recommended for Shared Hosting)

Most cPanel hosting (Namecheap, Hostinger, GoDaddy, A2 Hosting, etc.) includes the **"Setup Python App"** tool.

### 1. Upload & Extract Files
1. Log in to your **cPanel**.
2. Open **File Manager**.
3. Create a folder in your home directory, for example: `/home/yourusername/telegram_autopost`  
   *(Do NOT extract directly into `public_html`, extract into a separate folder in your home directory).*
4. Upload your ZIP file and click **Extract**.

### 2. Create the Python App in cPanel
1. In cPanel, search for **"Setup Python App"** (under the **Software** section) and open it.
2. Click **Create Application** (blue button).
3. Fill in the fields:
   - **Python version:** Select **3.10**, **3.11**, or **3.12**.
   - **Application root:** Enter `telegram_autopost` (the folder name from Step 1).
   - **Application URL:** Select your domain or subdomain (e.g., `yourdomain.com` or `bot.yourdomain.com`).
   - **Application startup file:** Type `passenger_wsgi.py`.
   - **Application Entry point:** Type `application`.
4. Click **CREATE** in the top right.

### 3. Install Dependencies
1. After creating, cPanel will show a command at the top, like:
   ```bash
   source /home/yourusername/virtualenv/telegram_autopost/3.11/bin/activate && cd /home/yourusername/telegram_autopost
   ```
2. Open **Terminal** in cPanel (or connect via SSH).
3. Paste that command and press **Enter**.
4. Run:
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```
   *(Alternatively, in the cPanel Python App GUI, scroll down to "Configuration files", type `requirements.txt` and click "Run Pip Install").*

### 4. Restart Application
1. Click **RESTART** in the Python App manager.
2. Open your website: `https://yourdomain.com`!
3. Log in with Super Admin: `admin` / `admin123`.

---

## 🛠️ Method 2: cPanel VPS / Dedicated Server (Terminal & Reverse Proxy)

If you have a cPanel VPS or server with root/terminal access where you run Python as a background service:

### 1. Upload Files
Upload files to `/home/yourusername/telegram_autopost`.

### 2. Install Packages
```bash
cd /home/yourusername/telegram_autopost
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Run in Background (nohup or pm2 or systemd)
```bash
nohup python3 main.py > app.log 2>&1 &
```

### 4. Setup Domain Reverse Proxy (.htaccess)
In `/home/yourusername/public_html/.htaccess` (or your subdomain folder), add:
```apache
<IfModule mod_rewrite.c>
    RewriteEngine On
    RewriteRule ^(.*)$ http://127.0.0.1:8080/$1 [P,L]
</IfModule>
```

---

## 🔒 Post-Deployment Checklist
1. **SSL Certificate:** Ensure cPanel **AutoSSL** or Let's Encrypt SSL is active on your domain (`https://`).
2. **Permissions:** Ensure the `data/` and `sessions/` directories have read/write permissions (`755`).
3. **Change Default Admin Password:** Log into the portal as `admin` and update the password.
