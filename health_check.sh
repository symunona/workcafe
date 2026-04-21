#!/usr/bin/env bash
# health_check.sh — Daily health check for workcafe scrapers
# Gathers service status, log errors, DB image rates, disk usage.
# Restarts dead services, then calls forge for an AI assessment.
# Output: log/YYYY-MM-DD.log

set -euo pipefail

LOG_DIR="/home/symunona/dev/workcafe/log"
DATE=$(date +%Y-%m-%d)
TIME=$(date +%H:%M:%S)
LOG_FILE="$LOG_DIR/$DATE.log"
DB="/home/symunona/dev/workcafe/data/seoul/cafedata.db"

SERVICES=(
  "workcafe-kakao-images"
  "workcafe-naver-images"
  "workcafe-google-images"
  "workcafe-api"
  "workcafe-frontend"
)

# ── Helper ──────────────────────────────────────────────────────────────────
log() { echo "$@" | tee -a "$LOG_FILE"; }
divider() { log ""; log "$(printf '─%.0s' {1..70})"; log ""; }

# ── Header ───────────────────────────────────────────────────────────────────
{
echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "  WorkCafe Daily Health Check — $DATE $TIME UTC"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo ""
} | tee -a "$LOG_FILE"

# ── 1. Service status & auto-restart ────────────────────────────────────────
log "## SERVICE STATUS"
log ""

RESTART_ATTEMPTED=()
for SVC in "${SERVICES[@]}"; do
  STATUS=$(systemctl --user is-active "$SVC" 2>/dev/null || echo "unknown")
  RESTART_COUNT=$(systemctl --user show "$SVC" --property=NRestarts --value 2>/dev/null || echo "?")
  SINCE=$(systemctl --user show "$SVC" --property=ActiveEnterTimestamp --value 2>/dev/null | sed 's/ UTC//' || echo "")

  if [[ "$STATUS" == "active" ]]; then
    log "  ✓ $SVC  [active since: $SINCE, restarts: $RESTART_COUNT]"
  else
    log "  ✗ $SVC  [status: $STATUS, restarts: $RESTART_COUNT]"
    # Attempt restart for image scrapers and API
    if [[ "$SVC" == workcafe-*-images || "$SVC" == "workcafe-api" ]]; then
      log "    → Attempting restart..."
      if systemctl --user start "$SVC" 2>>"$LOG_FILE"; then
        sleep 3
        NEW_STATUS=$(systemctl --user is-active "$SVC" 2>/dev/null || echo "failed")
        log "    → Restart result: $NEW_STATUS"
        RESTART_ATTEMPTED+=("$SVC:$NEW_STATUS")
      else
        log "    → Restart FAILED"
        RESTART_ATTEMPTED+=("$SVC:failed")
      fi
    fi
  fi
done

divider

# ── 2. Image scraping rates (last 24h) ───────────────────────────────────────
log "## IMAGE SCRAPING RATES (last 24h)"
log ""

sqlite3 "$DB" "
  SELECT
    provider,
    COUNT(*) as images_24h,
    ROUND(COUNT(*) / 24.0, 1) as per_hour,
    MAX(scraped_at) as last_scraped
  FROM images
  WHERE scraped_at >= datetime('now', '-24 hours')
  GROUP BY provider
  ORDER BY images_24h DESC;
" 2>/dev/null | while IFS='|' read -r prov count rate last; do
  log "  $prov: $count images ($rate/hr) — last: $last"
done

log ""
log "Total images in DB by provider:"
sqlite3 "$DB" "
  SELECT provider, COUNT(*) as total FROM images GROUP BY provider ORDER BY total DESC;
" 2>/dev/null | while IFS='|' read -r prov total; do
  CAFES_DONE=$(sqlite3 "$DB" "SELECT COUNT(DISTINCT cafe_id) FROM images WHERE provider='$prov';" 2>/dev/null)
  CAFES_TOTAL=$(sqlite3 "$DB" "SELECT COUNT(*) FROM scraped_cafes WHERE provider='$prov';" 2>/dev/null)
  log "  $prov: $total images across $CAFES_DONE/$CAFES_TOTAL scraped_cafes"
done

divider

# ── 3. Recent errors from journal (last 24h) ─────────────────────────────────
log "## RECENT ERRORS & WARNINGS (last 24h)"
log ""

for SVC in workcafe-kakao-images workcafe-naver-images workcafe-google-images; do
  ERRORS=$(journalctl --user -u "$SVC" --no-pager --since "24 hours ago" 2>/dev/null \
    | grep -iE "(ERROR|CRITICAL|exception|traceback|429|rate.limit|captcha|blocked|persistent)" \
    | tail -10 || true)
  if [[ -n "$ERRORS" ]]; then
    log "  [$SVC errors]"
    echo "$ERRORS" | sed 's/^/    /' | tee -a "$LOG_FILE"
    log ""
  else
    log "  [$SVC] No errors in last 24h"
  fi
done

divider

# ── 4. Last activity per scraper ─────────────────────────────────────────────
log "## LAST ACTIVITY PER SCRAPER (tail of journal)"
log ""

for SVC in workcafe-kakao-images workcafe-naver-images workcafe-google-images; do
  log "  [$SVC — last 3 log lines]"
  journalctl --user -u "$SVC" --no-pager -n 3 2>/dev/null \
    | sed 's/^/    /' | tee -a "$LOG_FILE" || true
  log ""
done

# Google-specific staleness check — warn if last log line is >2h old
GOOGLE_LAST_LOG=$(journalctl --user -u workcafe-google-images.service --no-pager -n 1 \
  --output=short-unix 2>/dev/null | awk '{print int($1)}')
if [[ -n "$GOOGLE_LAST_LOG" ]]; then
  NOW_UNIX=$(date +%s)
  AGE_MIN=$(( (NOW_UNIX - GOOGLE_LAST_LOG) / 60 ))
  if [[ "$AGE_MIN" -gt 120 ]]; then
    log "  !! workcafe-google-images: last log was ${AGE_MIN}min ago — likely stalled"
  else
    log "  workcafe-google-images: last log ${AGE_MIN}min ago (OK — slow scraper)"
  fi
fi

divider

# ── 5. Disk usage ────────────────────────────────────────────────────────────
log "## DISK USAGE"
log ""

DATA_DIR="/home/symunona/dev/workcafe/data/seoul"
DISK_TOTAL=$(df -h "$DATA_DIR" 2>/dev/null | awk 'NR==2{print $2}')
DISK_USED=$(df -h "$DATA_DIR" 2>/dev/null | awk 'NR==2{print $3}')
DISK_AVAIL=$(df -h "$DATA_DIR" 2>/dev/null | awk 'NR==2{print $4}')
DISK_PCT=$(df -h "$DATA_DIR" 2>/dev/null | awk 'NR==2{print $5}')
DISK_PCT_NUM=$(df "$DATA_DIR" 2>/dev/null | awk 'NR==2{gsub(/%/,"",$5); print $5}')
DISK_AVAIL_KB=$(df -k "$DATA_DIR" 2>/dev/null | awk 'NR==2{print $4}')
IMG_SIZE=$(du -sh "$DATA_DIR" 2>/dev/null | awk '{print $1}')

log "  Partition: $DISK_USED / $DISK_TOTAL ($DISK_PCT used, $DISK_AVAIL free)"
log "  Image data dir: $IMG_SIZE"

for PROV in kakao naver google; do
  SZ=$(du -sh "$DATA_DIR/$PROV" 2>/dev/null | awk '{print $1}' || echo "N/A")
  log "  $PROV images: $SZ"
done

# ── Disk safety shutdown at < 2GB free ──────────────────────────────────────────────
MIN_AVAIL_KB=2097152 # 2GB
if [[ "${DISK_AVAIL_KB:-0}" -lt "$MIN_AVAIL_KB" ]]; then
  log ""
  log "  !!! DISK FREE SPACE < 2GB — SHUTTING DOWN ALL IMAGE SCRAPERS !!!"
  log ""
  for SVC in workcafe-kakao-images workcafe-naver-images workcafe-google-images; do
    if systemctl --user is-active "$SVC" &>/dev/null; then
      systemctl --user stop "$SVC"
      # Prevent systemd from auto-restarting it
      systemctl --user disable "$SVC"
      log "  STOPPED + DISABLED: $SVC"
    fi
  done
  log ""
  log "  Services stopped. Re-enable manually once disk is cleared:"
  log "    systemctl --user enable --now workcafe-kakao-images"
  log "    systemctl --user enable --now workcafe-naver-images"
  log "    systemctl --user enable --now workcafe-google-images"
fi

divider

# ── 6. Restart summary ───────────────────────────────────────────────────────
if [[ ${#RESTART_ATTEMPTED[@]} -gt 0 ]]; then
  log "## RESTART ACTIONS TAKEN"
  log ""
  for entry in "${RESTART_ATTEMPTED[@]}"; do
    SVC="${entry%%:*}"
    RESULT="${entry##*:}"
    log "  Restarted $SVC → $RESULT"
  done
  divider
fi

# ── 7. Claude AI investigation & fix ────────────────────────────────────────
log "## AI INVESTIGATION & ACTIONS (claude)"
log ""

CLAUDE_PROMPT="You are doing a daily health check on a Seoul cafe image scraping system running on this machine.

## System overview
- workcafe-kakao-images: downloads photos from Kakao Maps REST API (~9,225 scraped_cafes total)
- workcafe-naver-images: downloads photos via Naver Maps Playwright (GraphQL, ~857 scraped_cafes)
- workcafe-google-images: downloads photos from Google Maps via Playwright (intentionally slow: 60-90s sleep between scraped_cafes, ~1,164 scraped_cafes). Expected rate: ~10-20 images/hr max. A rate near zero is only a problem if the service is also inactive or the last journal line is >2h old.
- All services: Restart=always, RestartSec=300
- DB: /home/symunona/dev/workcafe/data/seoul/cafedata.db (images table has scraped_at timestamps)
- Service files: ~/.config/systemd/user/workcafe-*-images.service
- Scrapers: /home/symunona/dev/workcafe/scraper/scraper_*_images_v*.py

## Today's pre-gathered status report
$(cat "$LOG_FILE")

## Kakao iterative cap logic (IMPORTANT)
The Kakao scraper downloads images in batches of 150 per cafe per run. It orders scraped_cafes by
image count ASC (fewest first), so it does one full pass over all scraped_cafes before starting a
second batch. When a full pass completes, the scraper exits normally (code 0) and systemd
restarts it after 300s — which automatically begins the next 150-image batch for each cafe.
The 'Cap reached (150 new images this run)' log line confirms the cap is working.
A 'Skip: all N images downloaded' means that cafe is fully done.

## Your tasks
1. Review the report above. Check if any scraper has zero rate or is stuck (no activity in last 2h).
2. For any dead/stalled service: check the last 20 journal lines for the root cause, then restart it.
3. Check disk usage — if < 2GB free, services were already stopped by this script. Log CRITICAL.
   If < 5GB free, warn with estimated days to limit at current rate.
4. **Kakao pass completion check**: Run this query to see progress:
   sqlite3 /home/symunona/dev/workcafe/data/seoul/cafedata.db \
     \"SELECT COUNT(*) as scraped_cafes, MIN(cnt) as min_imgs, MAX(cnt) as max_imgs, ROUND(AVG(cnt),1) as avg_imgs
      FROM (SELECT COUNT(*) as cnt FROM images WHERE provider='kakao' GROUP BY cafe_id);\"
   If the Kakao scraper has been inactive for >6h AND min_imgs >= 150 (all scraped_cafes have at least one
   full batch), restart it to begin the next 150-image batch:
     systemctl --user restart workcafe-kakao-images
5. Check if the Naver scraper logs 'already on disk' (old skip logic) — if so, restart it.
6. Write a brief assessment to stdout:
   - Overall status: HEALTHY / WARNING / CRITICAL
   - What you found and what actions you took (1-2 lines each)
   - Kakao pass status: which batch are we on (min_imgs / 150), how many scraped_cafes are fully done
   - Any issue that needs human attention

Be concise. Take actions directly — don't just describe what should be done."

# Run claude non-interactively with full tool access, working dir set to scraper
ASSESSMENT=$(cd /home/symunona/dev/workcafe && \
  claude --dangerously-skip-permissions \
    --print \
    --model sonnet \
    --output-format text \
    --no-session-persistence \
    "$CLAUDE_PROMPT" 2>&1 \
  || echo "[claude assessment unavailable — check API key]")

log "$ASSESSMENT"
divider

log "Health check complete — $DATE $TIME UTC"
log "Full log: $LOG_FILE"
