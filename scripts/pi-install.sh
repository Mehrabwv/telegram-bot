#!/usr/bin/env bash
# Sets the bot up on a Raspberry Pi (Raspberry Pi OS Lite, 64-bit): its own Python
# environment, the packages, and a service that starts the bot when the Pi boots and
# restarts it if it ever stops. Safe to run again (after a `git pull`, for example).
#
# Run it from the telegram-bot folder:   bash scripts/pi-install.sh
set -euo pipefail
cd "$(dirname "$0")/.."
DIR="$(pwd)"
ME="$(id -un)"

if [ ! -f config.py ]; then
  echo "config.py is missing. Copy it (and your other private files) from your laptop" >&2
  echo "first: see 'Run it on a Raspberry Pi' in README.md." >&2
  exit 1
fi
if [ "$(uname -m)" != "aarch64" ]; then
  echo "This is a 32-bit system. Install Raspberry Pi OS Lite (64-bit) instead:" >&2
  echo "the packages have no ready-made 32-bit versions and would take hours to build." >&2
  exit 1
fi

echo "== System packages"
sudo apt-get update
sudo apt-get install -y python3-venv git

echo "== Python environment (.venv) and packages (a few minutes on a Zero 2 W)"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

echo "== Service"
# The Pi has no clock battery: wait until the time is synced before starting the bot,
# so the schedule never runs on a wrong clock.
sudo systemctl enable systemd-time-wait-sync.service 2>/dev/null || true
sudo tee /etc/systemd/system/telegram-bot.service > /dev/null <<EOF
[Unit]
Description=Telegram bot and Briefing app
Wants=network-online.target time-sync.target
After=network-online.target time-sync.target

[Service]
User=$ME
WorkingDirectory=$DIR
ExecStart=$DIR/.venv/bin/python -u bot.py
Environment=PYTHONIOENCODING=utf-8
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable telegram-bot.service
sudo systemctl restart telegram-bot.service

echo
echo "Done. The bot is running and starts by itself when the Pi boots."
echo "  Time zone: $(timedatectl show -p Timezone --value)  (change: sudo timedatectl set-timezone America/Phoenix)"
echo "  Its output:   journalctl -u telegram-bot -f"
echo "  Restart it:   sudo systemctl restart telegram-bot"
echo "  Stop it:      sudo systemctl stop telegram-bot"
