#!/usr/bin/env bash
set -euo pipefail
WDIR="${1:?Usage: register_watchdog.sh <project_root>}"
UNIT_DIR="$HOME/.config/systemd/user"
VENV="$WDIR/venv/bin/python"

mkdir -p "$UNIT_DIR"

cat > "$UNIT_DIR/workcafe-watchdog.service" << EOF
[Unit]
Description=Workcafe scraper watchdog (health check + auto-restart)

[Service]
Type=oneshot
WorkingDirectory=$WDIR/scraper
ExecStart=$VENV $WDIR/scraper/watchdog.py
StandardOutput=journal
StandardError=journal
EOF

cat > "$UNIT_DIR/workcafe-watchdog.timer" << EOF
[Unit]
Description=Workcafe scraper watchdog timer (every 30 min)

[Timer]
OnBootSec=2min
OnUnitActiveSec=30min

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now workcafe-watchdog.timer
echo "Watchdog registered. Next run: $(systemctl --user show workcafe-watchdog.timer --property=NextElapseUSecRealtime --value)"
