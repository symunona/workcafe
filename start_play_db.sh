#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/scraper"

DB_PATH="../data/seoul/clean-data.db"
SOCKET_PATH="/tmp/workcafe_play_db.sock"
PID_FILE="/tmp/workcafe_play_db.pid"

source ../venv/bin/activate

# Start play db server
nohup python3 db_server.py --db "$DB_PATH" --socket "$SOCKET_PATH" --pid-file "$PID_FILE" --replace > log/play_db_server.log 2>&1 &
echo "Play DB server started. Wait 2 seconds..."
sleep 2
echo "Done."
