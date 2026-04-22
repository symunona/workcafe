#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

DB_DIR="data/seoul"
SRC_DB="$DB_DIR/scraped.db"
PLAY_DB="$DB_DIR/clean.db"

echo "Copying $SRC_DB to $PLAY_DB..."
rm -f "$PLAY_DB" "$PLAY_DB-wal" "$PLAY_DB-shm"
sqlite3 "$SRC_DB" ".backup $PLAY_DB"
sqlite3 "$PLAY_DB" "DELETE FROM scraped_cafes WHERE lat < 37.492 OR lat > 37.495 OR lon < 126.985 OR lon > 126.988"
echo "Done."
