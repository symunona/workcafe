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
import sqlite3
import argparse
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..', 'scraper', 'lib'))
sys.path.insert(0, _HERE)

from cafe_norm_utils import llm_generate
from db_client import DBClient

ENGLISHIFY_DB = os.path.abspath(os.path.join(_HERE, '..', 'data', 'seoul', 'englishify.db'))
BATCH_SIZE = 10


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


_SEP = re.compile(r'\s*(?:;|→|->|:)\s*')

def _parse_numbered(text: str, batch: list) -> dict:
    """Parse 'N. EnglishName' lines → {korean: english} matched by index."""
    result = {}
    for line in text.splitlines():
        m = re.match(r'^(\d+)[.)]\s*(.+)', line.strip())
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(batch):
                result[batch[idx]] = m.group(2).strip()
    return result


def _translate_one(kr: str, model: str) -> str:
    return llm_generate(
        f"Translate this Korean cafe name to English. "
        f"Use transliteration for brand names. "
        f"Return only the English name, nothing else: {kr}",
        max_tokens=30, model=model,
    ).strip()


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
    translated = 0
    t0 = time.time()
    print(_progress(already, 0, total, 0), end="", flush=True)

    for i in range(0, total, BATCH_SIZE):
        batch = pending[i:i + BATCH_SIZE]
        numbered = "\n".join(f"{j+1}. {name}" for j, name in enumerate(batch))
        prompt = (
            "Translate Korean cafe names to English. "
            "One per line as \"N. EnglishName\":\n\n"
            + numbered
        )
        pairs = {}
        try:
            result = llm_generate(prompt, max_tokens=len(batch) * 30, model=model)
            pairs = _parse_numbered(result, batch)
        except Exception:
            pass

        # retry missed names one-by-one
        missed = [kr for kr in batch if not pairs.get(kr)]
        if missed:
            print(f"\n  retrying {len(missed)}", end="", flush=True)
            for kr in missed:
                try:
                    pairs[kr] = _translate_one(kr, model)
                except Exception:
                    pass

        still_missing = sum(1 for kr in batch if not pairs.get(kr))
        for kr in batch:
            en = pairs.get(kr, "")
            if not en:
                continue
            cur.execute("""
                UPDATE name_translations
                   SET english_name = ?, model = ?, translated_at = datetime('now')
                 WHERE korean_name = ?
            """, (en, model, kr))
            translated += 1

        eng.commit()
        suffix = f"  skipped:{still_missing}" if still_missing else ""
        print(_progress(already, translated, total, time.time() - t0) + suffix, end="", flush=True)

    print()

    return translated


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build/update englishify.db translation cache")
    parser.add_argument("--socket",        default="/tmp/workcafe_play_db.sock",
                        help="DB socket (play DB pointing to clean.db)")
    parser.add_argument("--englishify-db", default=ENGLISHIFY_DB,
                        help="Path to englishify.db")
    parser.add_argument("--sync-only",     action="store_true",
                        help="Only sync names + chain pre-pass, skip ollama")
    parser.add_argument("--model",         default="qwen2.5:3b")
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
