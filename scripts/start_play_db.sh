#!/usr/bin/env bash
set -euo pipefail

WDIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$WDIR/scraper"

DB_PATH="$WDIR/data/seoul/clean.db"
SOCKET_PATH="/tmp/workcafe_play_db.sock"
PID_FILE="/tmp/workcafe_play_db.pid"

source "$WDIR/venv/bin/activate"

# Start play db server (--replace kills any existing server using the PID file)
nohup python3 db_server.py --db "$DB_PATH" --socket "$SOCKET_PATH" --pid-file "$PID_FILE" --replace --unsafe-any-db > log/play_db_server.log 2>&1 &

# Wait until socket is ready (up to 10s)
for i in $(seq 1 20); do
    [ -S "$SOCKET_PATH" ] && break
    sleep 0.5
done

if [ ! -S "$SOCKET_PATH" ]; then
    echo "ERROR: play db server socket not created after 10s. Check log/play_db_server.log"
    exit 1
fi
echo "Play DB server ready on $SOCKET_PATH"
