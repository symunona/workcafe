#!/usr/bin/env python3
"""
pipeline_daemon.py — unified real-time pipeline (Phase 1).

One process, config-driven, polling loop. Evolves merge_daemon.py: instead of a
single batch merge it runs an ordered two-step watcher (translate THEN merge,
"Order B" from plans/unified-pipeline.md) so freshly-scraped cafes flow
  scraped → translated → merged
into clean.db continuously, with the LLM forced onto CPU throughout (the GPU is
reserved for the RAM++ image tagger).

State lives in clean.db's scraped_cafes.status column (no queue table):
  - 00_sync copies new scraped.db rows → clean.db (status defaults to 'scraped').
  - translate step:  status='scraped' names lacking an english_name → translate
                     (qwen2.5:1.5b, num_gpu:0) → englishify.db; set status='translated'.
  - merge step:      when >= translate_batch 'translated' rows exist OR a debounce
                     timer fires → run 04_normalize (--merge-log --llm-cpu) + 06_link
                     against the play DB → merged rows get status='merged'; every
                     merge writes a merge_log row.

Reuses 04/05/06 — does not rewrite them. 05's translate logic is reused directly
(open_englishify / sync / chain pre-pass / ollama_batch) with CPU forced via a
patched llm_generate.

Config (data/pipeline.json, via --config):
    poll_interval_s, merge_debounce_s, translate_batch, llm_on_cpu, ...

Monitoring:
    journalctl --user -u workcafe-pipeline -f
    sqlite3 data/seoul/clean.db "SELECT status,COUNT(*) FROM scraped_cafes GROUP BY status;"
    sqlite3 data/seoul/clean.db "SELECT ts,method,detail FROM merge_log ORDER BY ts DESC LIMIT 30;"
"""
import os
import sys
import json
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
DEFAULT_CONFIG = PROJECT_ROOT / "data" / "pipeline.json"

sys.path.insert(0, str(PROJECT_ROOT / "scraper" / "lib"))
sys.path.insert(0, str(PROJECT_ROOT / "data-processing"))

_stop = False


def _handle_term(signum, _frame):
    global _stop
    _stop = True
    log(f"signal {signum} — finishing current step then exiting")


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def load_config(path):
    cfg = {
        "poll_interval_s": 30,
        "merge_debounce_s": 60,
        "translate_batch": 30,
        "image_priority_first_n": 30,
        "chain_promote_min": 5,
        "llm_on_cpu": True,
    }
    try:
        with open(path) as f:
            cfg.update(json.load(f))
    except Exception as e:
        log(f"config load failed ({e}); using defaults")
    return cfg


def run(cmd):
    subprocess.run(cmd, cwd=PROJECT_ROOT, check=True)


def ensure_play_db():
    """Make sure the persistent play DB server (clean.db) is up on PLAY_SOCK."""
    if os.path.exists(PLAY_SOCK):
        return
    log("play DB socket missing — starting play DB server (clean.db)")
    run(["bash", "scripts/start_play_db.sh"])


# ─── status counts (read-only against clean.db, WAL-safe) ─────────────────────

def _counts():
    if not CLEAN_DB.exists():
        return {}
    con = sqlite3.connect(f"file:{CLEAN_DB}?mode=ro", uri=True, timeout=30)
    try:
        rows = con.execute(
            "SELECT COALESCE(status,'scraped'), COUNT(*) FROM scraped_cafes GROUP BY 1"
        ).fetchall()
        return {r[0]: r[1] for r in rows}
    except sqlite3.OperationalError:
        return {}
    finally:
        con.close()


def _scraped_in_scraped_db():
    if not SCRAPED_DB.exists():
        return 0
    con = sqlite3.connect(f"file:{SCRAPED_DB}?mode=ro", uri=True, timeout=30)
    try:
        return con.execute("SELECT COUNT(*) FROM scraped_cafes").fetchone()[0]
    except sqlite3.OperationalError:
        return 0
    finally:
        con.close()


# ─── sync step (00) ───────────────────────────────────────────────────────────

def sync_step():
    """Copy new scraped.db rows → clean.db. New rows arrive with status='scraped'."""
    clean_n = _counts()
    clean_total = sum(clean_n.values())
    scraped_total = _scraped_in_scraped_db()
    if scraped_total <= clean_total:
        return 0
    log(f"sync: {scraped_total - clean_total} new scraped rows → clean.db")
    run([PY, "data-processing/00_sync_from_scraped.py"])
    return scraped_total - clean_total


# ─── translate step (reuse 05_englishify logic, CPU-forced) ───────────────────

def translate_step(cfg, dbc):
    """Translate names of status='scraped' cafes that lack an english_name.

    Reuses 05_englishify's open/sync/prepass/ollama_batch. Forces qwen onto CPU
    by patching cafe_norm_utils.llm_generate to pass cpu=llm_on_cpu. After the
    englishify cache is filled, flip every 'scraped' cafe whose name now has an
    english translation to status='translated'.
    Returns number of cafes promoted to 'translated'.
    """
    import importlib
    import cafe_norm_utils
    englishify = importlib.import_module("05_englishify")

    # Force CPU on the per-name translation path used by 05 (both batched + retry).
    if cfg["llm_on_cpu"] and not getattr(cafe_norm_utils, "_cpu_patched", False):
        _orig = cafe_norm_utils.llm_generate

        def _cpu_llm(prompt, max_tokens=100, model=cafe_norm_utils.LLM_MODEL, cpu=False):
            return _orig(prompt, max_tokens=max_tokens, model=model, cpu=True)

        cafe_norm_utils.llm_generate = _cpu_llm
        englishify.llm_generate = _cpu_llm  # 05 imported the symbol by name
        cafe_norm_utils._cpu_patched = True
        log("translate: LLM forced to CPU (num_gpu=0)")

    eng = englishify.open_englishify(str(ENGLISHIFY_DB))
    try:
        englishify.sync_names(dbc, eng)
        englishify.chain_prepass(dbc, eng)
        englishify.google_native_prepass(dbc, eng)

        # Cap each cycle at translate_batch * BATCH_SIZE names so a huge backlog
        # streams in chunks (merge can start on the first chunk; debounce handles it).
        cap = cfg["translate_batch"] * englishify.BATCH_SIZE
        pending = eng.execute(
            "SELECT COUNT(*) FROM name_translations WHERE english_name IS NULL"
        ).fetchone()[0]
        if pending:
            log(f"translate: {pending} names lacking english_name "
                f"(translating up to {cap} this cycle, on CPU)")
            n = _ollama_batch_capped(englishify, eng, cap,
                                     model="qwen2.5:1.5b")
            log(f"translate: +{n} new translations")
    finally:
        eng.close()

    # Promote 'scraped' cafes whose name now has an english_name → 'translated'.
    return _promote_translated(dbc)


def _promote_translated(dbc):
    """Promote scraped→translated for names present in englishify.db.

    db_server can't ATTACH englishify.db, so read translated names here and push
    an UPDATE keyed by name. Chunked to keep SQL param lists sane.
    """
    eng = sqlite3.connect(f"file:{ENGLISHIFY_DB}?mode=ro", uri=True, timeout=30)
    try:
        names = [r[0] for r in eng.execute(
            "SELECT korean_name FROM name_translations WHERE english_name IS NOT NULL"
        ).fetchall()]
    finally:
        eng.close()
    if not names:
        return 0
    translated = set(names)

    # Pull the still-'scraped' unmerged cafes and update those whose name is translated.
    rows = dbc.fetchall(
        "SELECT id, name FROM scraped_cafes "
        "WHERE COALESCE(status,'scraped')='scraped' AND belongs_to_cafe_id IS NULL"
    )
    ids = [r[0] for r in rows if r[1] in translated]
    if not ids:
        return 0
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        ph = ",".join("?" for _ in chunk)
        dbc.execute(
            f"UPDATE scraped_cafes SET status='translated', translated_at=datetime('now') "
            f"WHERE id IN ({ph})",
            chunk,
        )
    return len(ids)


def _ollama_batch_capped(englishify, eng, cap, model):
    """Run 05's ollama_batch but only on the first `cap` pending names this cycle."""
    if cap <= 0:
        return englishify.ollama_batch(eng, model=model)
    # 05.ollama_batch translates ALL pending; to cap, temporarily hide the rest by
    # processing a bounded slice ourselves using 05's helpers.
    pending = [r[0] for r in eng.execute(
        "SELECT korean_name FROM name_translations WHERE english_name IS NULL "
        "LIMIT ?", (cap,)).fetchall()]
    if not pending:
        return 0
    translated = 0
    BS = englishify.BATCH_SIZE
    cur = eng.cursor()
    for i in range(0, len(pending), BS):
        batch = pending[i:i + BS]
        numbered = "\n".join(f"{j+1}. {name}" for j, name in enumerate(batch))
        prompt = ("Translate Korean cafe names to English. "
                  "One per line as \"N. EnglishName\":\n\n" + numbered)
        pairs = {}
        try:
            result = englishify.llm_generate(prompt, max_tokens=len(batch) * 30, model=model)
            pairs = englishify._parse_numbered(result, batch)
        except Exception:
            pass
        for kr in [k for k in batch if not pairs.get(k)]:
            try:
                pairs[kr] = englishify._translate_one(kr, model)
            except Exception:
                pass
        for kr in batch:
            en = pairs.get(kr, "")
            if not en:
                continue
            cur.execute(
                "UPDATE name_translations SET english_name=?, model=?, translated_at=datetime('now') "
                "WHERE korean_name=?", (en, model, kr))
            translated += 1
        eng.commit()
    return translated


# ─── merge step (reuse 04 + 06) ───────────────────────────────────────────────

def merge_step(cfg):
    """Run normalize (04, merge-log + CPU LLM) + link (06) against the play DB,
    then flip merged rows to status='merged'."""
    ensure_play_db()
    llm_cpu = ["--llm-cpu"] if cfg["llm_on_cpu"] else []
    run([PY, "data-processing/04_normalize_pipeline.py",
         "--db", str(CLEAN_DB), "--socket", PLAY_SOCK,
         "--englishify-db", str(ENGLISHIFY_DB), "--no-backup",
         "--merge-log"] + llm_cpu)
    run([PY, "data-processing/06_update_image_links.py", "--socket", PLAY_SOCK])

    # Mark everything that now has a clean_cafe as merged (status flow terminal state).
    from db_client import DBClient
    dbc = DBClient(socket_path=PLAY_SOCK)
    resp = dbc.execute(
        "UPDATE scraped_cafes SET status='merged' "
        "WHERE belongs_to_cafe_id IS NOT NULL AND COALESCE(status,'scraped') != 'merged'"
    )
    return resp.get("rowcount", 0)


# ─── chain promote (kept minimal — 03 occasionally) ──────────────────────────

def chains_step():
    ensure_play_db()
    run([PY, "data-processing/03_detect_chains.py", "--socket", PLAY_SOCK])


def interruptible_sleep(seconds):
    for _ in range(int(seconds)):
        if _stop:
            return
        time.sleep(1)


def fmt_counts(c):
    order = ["scraped", "translated", "merged"]
    parts = [f"{k}={c.get(k, 0)}" for k in order]
    extra = [f"{k}={v}" for k, v in c.items() if k not in order]
    return " ".join(parts + extra)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--chain-every", type=int, default=20,
                    help="run 03_detect_chains every Nth merge cycle (global, expensive)")
    args = ap.parse_args()

    signal.signal(signal.SIGTERM, _handle_term)
    signal.signal(signal.SIGINT, _handle_term)

    cfg = load_config(args.config)
    log(f"pipeline daemon up — config={args.config} "
        f"poll={cfg['poll_interval_s']}s debounce={cfg['merge_debounce_s']}s "
        f"translate_batch={cfg['translate_batch']} llm_on_cpu={cfg['llm_on_cpu']}")

    ensure_play_db()
    from db_client import DBClient
    dbc = DBClient(socket_path=PLAY_SOCK)

    cycles = 0
    translated_since = None  # time the first 'translated' row started waiting

    while not _stop:
        try:
            # 1) sync new scraped rows into clean.db
            sync_step()

            # 2) translate any 'scraped' rows lacking english_name (CPU)
            promoted = translate_step(cfg, dbc)
            if promoted:
                log(f"translate: {promoted} cafes → status='translated'")

            counts = _counts()
            n_translated = counts.get("translated", 0)
            now = time.time()
            if n_translated > 0 and translated_since is None:
                translated_since = now
            elif n_translated == 0:
                translated_since = None

            # 3) merge when enough translated rows OR debounce elapsed
            debounce_ready = (translated_since is not None and
                              now - translated_since >= cfg["merge_debounce_s"])
            trigger = (n_translated >= cfg["translate_batch"]) or \
                      (n_translated > 0 and debounce_ready)

            if trigger:
                log(f"── merge cycle {cycles} start: {fmt_counts(counts)} ──")
                if cycles % args.chain_every == 0:
                    chains_step()
                merged = merge_step(cfg)
                cycles += 1
                translated_since = None
                log(f"── merge cycle done: {merged} → merged | {fmt_counts(_counts())} ──")
                continue  # re-check immediately in case backlog remains

            log(f"idle: {fmt_counts(counts)} "
                f"(translated waiting {int(now - translated_since) if translated_since else 0}s)")
        except subprocess.CalledProcessError as e:
            log(f"step FAILED ({e}); backing off {cfg['poll_interval_s']}s")
        except Exception as e:
            log(f"unexpected error ({e}); backing off {cfg['poll_interval_s']}s")

        interruptible_sleep(cfg["poll_interval_s"])

    log("pipeline daemon stopped")


if __name__ == "__main__":
    main()
