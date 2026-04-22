#!/usr/bin/env bash
# backup-clean.sh — snapshot clean.db to data/seoul/history/clean_YYYY-MM-DD-vN.db
# Usage: ./scripts/backup-clean.sh [--min-cafes N] [--db PATH]
# Skips if cafe count < min_cafes (default 1000) to avoid backing up empty/reset DBs.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$SCRIPT_DIR/.."
DB="${1:-$ROOT/data/seoul/clean.db}"
HISTORY_DIR="$ROOT/data/seoul/history"
MIN_CAFES=1000

# parse flags
while [[ $# -gt 0 ]]; do
  case "$1" in
    --min-cafes) MIN_CAFES="$2"; shift 2 ;;
    --db) DB="$2"; shift 2 ;;
    *) shift ;;
  esac
done

if [ ! -f "$DB" ]; then
  echo "ERROR: DB not found: $DB" >&2
  exit 1
fi

# count clean cafes
CAFE_COUNT=$(sqlite3 "$DB" "SELECT COUNT(*) FROM clean_cafes;" 2>/dev/null || echo 0)
if [ "$CAFE_COUNT" -lt "$MIN_CAFES" ]; then
  echo "SKIP: only $CAFE_COUNT cafes in clean.db (min=$MIN_CAFES) — not worth backing up"
  exit 0
fi

mkdir -p "$HISTORY_DIR"

DATE=$(date +%Y-%m-%d)
V=1
while [ -f "$HISTORY_DIR/clean_${DATE}-v${V}.db" ]; do
  V=$((V+1))
done
NAME="clean_${DATE}-v${V}"

echo "Backing up $DB → $HISTORY_DIR/${NAME}.db ($CAFE_COUNT cafes)..."

# WAL checkpoint before copy so the backup is self-contained
sqlite3 "$DB" "PRAGMA wal_checkpoint(TRUNCATE);" > /dev/null 2>&1 || true
cp "$DB" "$HISTORY_DIR/${NAME}.db"

# gather stats for the .md
CHAIN_COUNT=$(sqlite3 "$DB" "SELECT COUNT(*) FROM cafe_chains;" 2>/dev/null || echo "?")
PROVIDERS=$(sqlite3 "$DB" "SELECT providers, COUNT(*) as n FROM clean_cafes GROUP BY providers ORDER BY n DESC;" 2>/dev/null || echo "?")
WITH_IMAGES=$(sqlite3 "$DB" "SELECT COUNT(DISTINCT c.belongs_to_cafe_id) FROM images i JOIN scraped_cafes c ON c.id = i.cafe_id WHERE c.belongs_to_cafe_id IS NOT NULL AND i.file_size > 0;" 2>/dev/null || echo "?")

cat > "$HISTORY_DIR/${NAME}.md" << EOF
# ${NAME}

**Date:** ${DATE}
**Total cafes:** ${CAFE_COUNT}
**Chains:** ${CHAIN_COUNT}
**Cafes with images:** ${WITH_IMAGES}

## Provider breakdown

\`\`\`
${PROVIDERS}
\`\`\`

## Notes

<!-- Add your findings here -->

EOF

echo "Done: $HISTORY_DIR/${NAME}.db + ${NAME}.md"
