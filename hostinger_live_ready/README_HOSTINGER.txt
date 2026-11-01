====================================================================
     TELEGRAM AUTOPOST PRO - HOSTINGER PERMANENT SETUP GUIDE
====================================================================

Namaste! Hostinger par permanent live chalane ke liye steps:

--------------------------------------------------------------------
STEP 1: HOSTINGER PAR NAYA ZIP EXTRACT KAREN
--------------------------------------------------------------------
1. Hostinger hPanel -> File Manager kholiye.
2. `public_html/telegrambot/` folder me jaiye.
3. Purani files ko delete karke `hostinger_live_ready.zip` upload aur EXTRACT kar dijiye.
   * Is nayi ZIP me Auto-Sync Webhook shamil hai jisse aapka domain kabhi disconnect nahi hoga.

--------------------------------------------------------------------
STEP 2: APNE COMPUTER PAR WATCHDOG CHALAYEIN (PERMANENT AUTO-SYNC)
--------------------------------------------------------------------
Aapko baar-baar tunnel URL copy-paste karne ki zaroorat nahi hai!
1. Apne folder (`D:\Sam Telegram\`) me jaakar:
   `RUN_AUTOPOST_SERVICE.bat` par double click karein.
2. Ye batch file:
   - Backend Python server ko start karega.
   - Cloudflare tunnel ko start karega.
   - Hostinger ke sath real-time me auto-sync karega.
   - Agar tunnel reconnect hota hai ya naya URL banta hai, ye apne aap 2 second me Hostinger ko update kar dega!
3. Jab tak ye window khula rahega, aapki website 100% live aur working rahegi.

--------------------------------------------------------------------
STEP 3: AGAR 24/7 BINA PC ON RAKHE CHALANA HO (FREE CLOUD HOSTING)
--------------------------------------------------------------------
Agar aap chahte hain ki aapka computer band hone par bhi bot 24 ghante chalta rahe:
1. Render.com ya Koyeb par free account banayein.
2. Humne `Dockerfile`, `render.yaml`, aur `requirements.txt` ready kar diye hain.
3. Render par deploy karne ke baad Render aapko ek permanent URL dega (e.g. `https://mybot.onrender.com`).
4. Bas wahi URL Hostinger ke `config.php` me daal dijiye:
   $BACKEND_URL = "https://mybot.onrender.com";
5. Iske baad aapka PC band bhi rahega to bhi website aur bot 24/7 chalte rahenge!

====================================================================
ADMIN CREDENTIALS:
- Email    : simardeep647.ss@gmail.com
- Username : admin
- Password : admin123
====================================================================
