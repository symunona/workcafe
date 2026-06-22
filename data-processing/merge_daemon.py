#!/usr/bin/env python3
"""
merge_daemon.py — streaming incremental merge.

Runs as the `workcafe-merge` service alongside the scrapers. Instead of the
batch `just merge-pipeline`, it polls the merge watermark and runs the
incremental merge chain whenever enough new cafes have accumulated (or a
debounce timer fires), so scraped cafes flow into clean.db continuously.

Watermark (no message bus — stages chain by DB columns):
    pending = (scraped.db rows not yet in clean.db)        # un-synced
            + (clean.db scraped_cafes WHERE belongs_to_cafe_id IS NULL)  # un-merged
    A scraper sets the first term by writing scraped.db; this daemon clears it
    by syncing + merging; the tagger then picks up the newly-linked images.

Per cycle (a subset of `just merge-pipeline`, run against the persistent play DB):
    00_sync       copy new scraped rows → clean.db
    03_chains     (only every MERGE_CHAIN_EVERY cycles — global + expensive)
    05_englishify translate new names → englishify.db
    04_normalize  spatial-merge new cafes → clean_cafes
    06_link       link new images → clean_cafes

Trigger:
    pending >= MERGE_BATCH_THRESHOLD            (enough work — merge now), OR
    pending  > 0 and waited >= MERGE_MAX_WAIT   (debounce — don't starve a small batch)
Poll every MERGE_POLL_INTERVAL seconds in between.

Env knobs (all optional):
    MERGE_POLL_INTERVAL   default 60   seconds between watermark checks
    MERGE_BATCH_THRESHOLD default 200  pending cafes that trigger an immediate cycle
    MERGE_MAX_WAIT        default 600  seconds before a non-empty batch is merged anyway
    MERGE_CHAIN_EVERY     default 10   run chain detection every Nth cycle
"""
import os
import sys
import time
import signal
import sqlite3
import subprocess
from pathlib import Path

PROJECT_ROOT  = Path(__file__).resolve().parents[1]
PY            = str(PROJECT_ROOT / "venv" / "bin" / "python3")
SCRAPED_DB    = PROJECT_ROOT / "data" / "seoul" / "scraped.db"
CLEAN_DB      = PROJECT_ROOT / "data" / "seoul" / "clean.db"
ENGLISHIFY_DB = PROJECT_ROOT / "data" / "seoul" / "englishify.db"
PLAY_SOCK     = "/tmp/workcafe_play_db.sock"

POLL_INTERVAL   = int(os.environ.get("MERGE_POLL_INTERVAL",   "60"))
BATCH_THRESHOLD = int(os.environ.get("MERGE_BATCH_THRESHOLD", "200"))
MAX_WAIT        = int(os.environ.get("MERGE_MAX_WAIT",        "600"))
CHAIN_EVERY     = int(os.environ.get("MERGE_CHAIN_EVERY",     "10"))

_stop = False


def _handle_term(signum, _frame):
    global _stop
    _stop = True
    log(f"signal {signum} — finishing current cycle then exiting")


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def _count(db_path, sql):
    """Read-only COUNT against a DB file (WAL-safe alongside the play server)."""
    if not Path(db_path).exists():
        return 0
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
    try:
        return con.execute(sql).fetchone()[0]
    except sqlite3.OperationalError:
        return 0  # table not created yet on a fresh DB
    finally:
        con.close()


def pending():
    """New rows not yet synced into clean.db, plus synced-but-unmerged rows."""
    scraped = _count(SCRAPED_DB, "SELECT COUNT(*) FROM scraped_cafes")
    clean   = _count(CLEAN_DB,   "SELECT COUNT(*) FROM scraped_cafes")
    unmerged = _count(CLEAN_DB,
                      "SELECT COUNT(*) FROM scraped_cafes WHERE belongs_to_cafe_id IS NULL")
    return max(scraped - clean, 0) + unmerged


def run(cmd):
    subprocess.run(cmd, cwd=PROJECT_ROOT, check=True)


def ensure_play_db():
    """Make sure the persistent play DB server (clean.db) is up on PLAY_SOCK.

    Normally started by the workcafe-play-db service; started here too so the
    daemon also works when launched standalone.
    """
    if os.path.exists(PLAY_SOCK):
        return
    log("play DB socket missing — starting play DB server (clean.db)")
    run(["bash", "scripts/start_play_db.sh"])


def cycle(run_chains: bool):
    ensure_play_db()
    run([PY, "data-processing/00_sync_from_scraped.py"])
    if run_chains:
        run([PY, "data-processing/03_detect_chains.py", "--socket", PLAY_SOCK])
    run([PY, "data-processing/05_englishify.py", "--socket", PLAY_SOCK])
    run([PY, "data-processing/04_normalize_pipeline.py",
         "--db", str(CLEAN_DB), "--socket", PLAY_SOCK,
         "--englishify-db", str(ENGLISHIFY_DB), "--no-backup"])
    run([PY, "data-processing/06_update_image_links.py", "--socket", PLAY_SOCK])


def interruptible_sleep(seconds):
    for _ in range(seconds):
        if _stop:
            return
        time.sleep(1)


def main():
    signal.signal(signal.SIGTERM, _handle_term)
    signal.signal(signal.SIGINT, _handle_term)

    log(f"merge daemon up — poll={POLL_INTERVAL}s threshold={BATCH_THRESHOLD} "
        f"max_wait={MAX_WAIT}s chain_every={CHAIN_EVERY}")
    ensure_play_db()

    cycles = 0
    waiting_since = None

    while not _stop:
        p = pending()
        now = time.time()

        if p <= 0:
            waiting_since = None
        elif waiting_since is None:
            waiting_since = now

        trigger = (p >= BATCH_THRESHOLD) or \
                  (p > 0 and waiting_since is not None and now - waiting_since >= MAX_WAIT)

        if trigger:
            run_chains = (cycles % CHAIN_EVERY == 0)
            log(f"── cycle {cycles} start: pending={p} chains={run_chains} ──")
            try:
                cycle(run_chains)
            except subprocess.CalledProcessError as e:
                log(f"cycle FAILED ({e}); backing off {POLL_INTERVAL}s")
                interruptible_sleep(POLL_INTERVAL)
                continue
            cycles += 1
            waiting_since = None
            log(f"── cycle done (remaining pending≈{pending()}) ──")
            continue  # re-check immediately in case a large backlog remains

        interruptible_sleep(POLL_INTERVAL)

    log("merge daemon stopped")


if __name__ == "__main__":
    main()
