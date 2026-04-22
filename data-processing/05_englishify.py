#!/usr/bin/env python3
"""
05_englishify.py — Build/maintain englishify.db: a persistent Korean→English name cache.

Steps:
  1. Sync all distinct names from scraped_cafes → englishify.db (INSERT OR IGNORE)
  2. Chain pre-pass: fill english_name from cafe_chains (free, no LLM)
  3. Ollama batch translation for remaining NULLs

Safe to re-run: idempotent. Never touches scraped.db or clean.db directly.
Output: data/seoul/englishify.db  (lookup table used by 04_normalize_pipeline.py)
"""
import os
import sys
import re
import json
import sqlite3
import argparse
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..', 'scraper', 'lib'))
sys.path.insert(0, _HERE)

from cafe_norm_utils import llm_generate
from db_client import DBClient

ENGLISHIFY_DB = os.path.abspath(os.path.join(_HERE, '..', 'data', 'seoul', 'englishify.db'))
BATCH_SIZE = 30


def open_englishify(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS name_translations (
            korean_name   TEXT PRIMARY KEY,
            english_name  TEXT,
            model         TEXT,
            translated_at TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nt_english ON name_translations(english_name) WHERE english_name IS NOT NULL")
    conn.commit()
    return conn


def sync_names(dbc: DBClient, eng: sqlite3.Connection) -> int:
    """Copy all distinct names from scraped_cafes → name_translations (INSERT OR IGNORE)."""
    rows = dbc.fetchall("SELECT DISTINCT name FROM scraped_cafes WHERE name IS NOT NULL AND name != ''")
    cur = eng.cursor()
    new_count = 0
    for (name,) in rows:
        cur.execute("INSERT OR IGNORE INTO name_translations(korean_name) VALUES (?)", (name,))
        new_count += cur.rowcount
    eng.commit()
    print(f"  sync: {new_count} new names added ({len(rows)} total distinct names)")
    return len(rows)


def chain_prepass(dbc: DBClient, eng: sqlite3.Connection) -> int:
    """Fill english_name from cafe_chains — free, no LLM call."""
    chains = dbc.fetchall(
        "SELECT name, name_english FROM cafe_chains WHERE name_english IS NOT NULL AND name_english != ''"
    )
    cur = eng.cursor()
    count = 0
    for (kr, en) in chains:
        cur.execute("""
            UPDATE name_translations
               SET english_name = ?, model = 'chain_lookup', translated_at = datetime('now')
             WHERE korean_name = ? AND english_name IS NULL
        """, (en, kr))
        count += cur.rowcount
    eng.commit()
    print(f"  chain pre-pass: filled {count} names")
    return count


def _progress(already: int, done: int, total: int, elapsed: float, bar_len: int = 30) -> str:
    """Progress bar anchored at `already` (pre-existing), counting `done` new translations."""
    grand_total = already + total
    grand_done  = already + done
    pct = grand_done / grand_total if grand_total > 0 else 0
    filled = int(bar_len * pct)
    bar = "█" * filled + "░" * (bar_len - filled)
    eta = ""
    if done > 0 and elapsed > 0:
        rate = done / elapsed
        remaining = (total - done) / rate
        m, s = divmod(int(remaining), 60)
        eta = f"  ETA {m}m{s:02d}s  {rate:.1f}/s"
    return f"\r[{bar}] {grand_done}/{grand_total} ({pct*100:.1f}%){eta}  "


def ollama_batch(eng: sqlite3.Connection, model: str = "qwen2.5:1.5b") -> int:
    cur = eng.cursor()
    cur.execute("SELECT korean_name FROM name_translations WHERE english_name IS NULL")
    pending = [r[0] for r in cur.fetchall()]
    already = eng.execute("SELECT COUNT(*) FROM name_translations WHERE english_name IS NOT NULL").fetchone()[0]

    if not pending:
        print(f"  ollama: nothing to translate ({already} already done)")
        return 0

    total = len(pending)
    print(f"  ollama: {already} already translated, {total} remaining — batches of {BATCH_SIZE}")
    translated = 0
    t0 = time.time()

    for i in range(0, total, BATCH_SIZE):
        batch = pending[i:i + BATCH_SIZE]
        prompt = (
            "Translate these Korean cafe/coffee shop names to English. "
            "Use transliteration for brand names (e.g. 스타벅스 → Starbucks). "
            "Return ONLY a JSON array of strings, same order, no explanation:\n"
            + json.dumps(batch, ensure_ascii=False)
        )
        try:
            result = llm_generate(prompt)
            m = re.search(r'\[.*?\]', result, re.DOTALL)
            if m:
                translations = json.loads(m.group())
                for kr, en in zip(batch, translations):
                    cur.execute("""
                        UPDATE name_translations
                           SET english_name = ?, model = ?, translated_at = datetime('now')
                         WHERE korean_name = ?
                    """, (en.strip(), model, kr))
                    translated += 1
            else:
                raise ValueError(f"No JSON array in response: {result[:200]}")
        except Exception as e:
            print(f"\n  batch {i // BATCH_SIZE} failed ({e}), retrying one-by-one...")
            for kr in batch:
                try:
                    en = llm_generate(
                        f"Translate this Korean cafe name to English (one name only, brand transliteration): {kr}"
                    ).strip()
                    cur.execute("""
                        UPDATE name_translations
                           SET english_name = ?, model = ?, translated_at = datetime('now')
                         WHERE korean_name = ?
                    """, (en, f"{model}-single", kr))
                    translated += 1
                except Exception as e2:
                    print(f"    failed: {kr!r}: {e2}")
        eng.commit()
        print(_progress(already, translated, total, time.time() - t0), end="", flush=True)

    print()  # newline after progress bar

    return translated


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build/update englishify.db translation cache")
    parser.add_argument("--socket",        default="/tmp/workcafe_play_db.sock",
                        help="DB socket (play DB pointing to clean.db)")
    parser.add_argument("--englishify-db", default=ENGLISHIFY_DB,
                        help="Path to englishify.db")
    parser.add_argument("--sync-only",     action="store_true",
                        help="Only sync names + chain pre-pass, skip ollama")
    parser.add_argument("--model",         default="qwen2.5:1.5b")
    args = parser.parse_args()

    t0 = time.time()
    print(f"englishify.db: {args.englishify_db}")

    dbc = DBClient(socket_path=args.socket)
    eng = open_englishify(args.englishify_db)

    print("Step 1: sync names from scraped_cafes...")
    sync_names(dbc, eng)

    print("Step 2: chain pre-pass...")
    chain_prepass(dbc, eng)

    if not args.sync_only:
        print("Step 3: ollama batch translation...")
        n = ollama_batch(eng, model=args.model)
        print(f"  done: {n} new translations")
    else:
        pending = eng.execute("SELECT COUNT(*) FROM name_translations WHERE english_name IS NULL").fetchone()[0]
        print(f"  skipped ollama ({pending} names still untranslated)")

    total = eng.execute("SELECT COUNT(*) FROM name_translations").fetchone()[0]
    done  = eng.execute("SELECT COUNT(*) FROM name_translations WHERE english_name IS NOT NULL").fetchone()[0]
    print(f"\nenglishify.db: {done}/{total} names translated  ({time.time()-t0:.1f}s)")
    eng.close()
