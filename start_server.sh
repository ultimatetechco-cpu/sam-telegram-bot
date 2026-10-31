#!/bin/bash
# ---------------------------------------------------------
# Telegram AutoPost Pro - Linux / cPanel Background Runner
# ---------------------------------------------------------
cd "$(dirname "$0")"

# Activate virtualenv if it exists
if [ -d "venv" ]; then
    source venv/bin/activate
elif [ -f "../../virtualenv/bin/activate" ]; then
    source ../../virtualenv/bin/activate
fi

# Install dependencies if missing
python3 -m pip install -r requirements.txt --quiet

# Run server in background on port 8080
nohup python3 main.py > server.log 2>&1 &
echo "Telegram AutoPost Server started in background! PID: $!"
