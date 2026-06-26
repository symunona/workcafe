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

from cafe_norm_utils import (
    llm_generate, choose_english_name, is_latin,
    is_chain_cafe, strip_branch, brand_token,
)
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
    """Fill english_name from cafe_chains — free, no LLM call.

    Two passes:
      1. Exact: korean_name == chain.name (fast bulk UPDATE).
      2. Branch-aware: a cafe name like '할리스 부산해운대점' carries a branch suffix and
         never equals the bare chain key '할리스커피', so the exact pass misses it and
         it falls through to the LLM, which phonetically romanizes the brand
         ('할리스' → 'Halles'). Recognize the brand (known global brands via
         is_chain_cafe; dynamic chains via a brand-token / branch-stripped index)
         and fill the chain's canonical English name instead.
    """
    chains = dbc.fetchall(
        "SELECT name, name_english FROM cafe_chains WHERE name_english IS NOT NULL AND name_english != ''"
    )
    cur = eng.cursor()
    count = 0
    # Pass 1 — exact key match (bulk).
    for (kr, en) in chains:
        cur.execute("""
            UPDATE name_translations
               SET english_name = ?, model = 'chain_lookup', translated_at = datetime('now')
             WHERE korean_name = ? AND english_name IS NULL
        """, (en, kr))
        count += cur.rowcount
    eng.commit()

    # Pass 2 — branch-aware brand recognition for the leftovers. O(1) lookups only:
    # is_chain_cafe(name, []) restricts to the cheap CHAIN_MAPPING known-brand scan
    # (no per-name fuzzy loop over 900+ chains), and brand_index covers dynamically
    # detected chains by their branch-stripped / brand-token form.
    brand_index: dict = {}
    for kr, en in chains:
        for key in (strip_branch(kr), brand_token(kr)):
            if key and key not in brand_index:
                brand_index[key] = en
    pending = cur.execute(
        "SELECT korean_name FROM name_translations WHERE english_name IS NULL"
    ).fetchall()
    branch_count = 0
    for (name,) in pending:
        english = None
        res = is_chain_cafe(name, [])  # CHAIN_MAPPING only → canonical English
        if res["is_chain"]:
            english = res["chain_name"]
        else:
            for key in (strip_branch(name), brand_token(name)):
                if key in brand_index:
                    english = brand_index[key]
                    break
        if english and is_latin(english):
            cur.execute("""
                UPDATE name_translations
                   SET english_name = ?, model = 'chain_lookup_branch', translated_at = datetime('now')
                 WHERE korean_name = ? AND english_name IS NULL
            """, (english, name))
            branch_count += cur.rowcount
    eng.commit()
    print(f"  chain pre-pass: filled {count} exact + {branch_count} branch-aware names")
    return count + branch_count


def google_native_prepass(dbc: DBClient, eng: sqlite3.Connection,
                           llm_pick: bool = True) -> int:
    """Adopt Google's native English name into the translation cache — smartly.

    Google sometimes scrapes a real English name (e.g. "The Han River Cafe"); that
    beats any LLM translation of the Korean name. But Google also returns phonetic
    romanizations (디졸브 → "Dijolbeu") that are WORSE than a real translation. So we
    run the shared `choose_english_name` classifier (anchor-word → take Google;
    romanization fingerprint → keep current; grey zone → 1 qwen call) instead of
    blind-accepting any latin string.

    Crucially this runs EVERY cycle and overwrites name_translations rows whose
    model != 'google_native' when the classifier picks the Google name — so a LATE
    Google arrival replaces an earlier LLM translation already in the cache.

    Keyed by the clean_cafe's Korean name so future normalize runs inherit it, and
    the 04 propagate step refreshes already-merged clean_cafes.
    """
    rows = dbc.fetchall("""
        SELECT sc.name, cc.name
        FROM scraped_cafes sc
        JOIN clean_cafes cc ON sc.belongs_to_cafe_id = cc.id
        WHERE sc.provider = 'google'
          AND sc.name IS NOT NULL
          AND sc.name != ''
    """)
    cur = eng.cursor()
    stats = {'anchor': 0, 'no-current': 0, 'llm': 0, 'romanization-reject': 0,
             'skip-latin': 0, 'skip-same': 0}
    count = 0
    seen_kr: set = set()
    for (google_name, cc_korean_name) in rows:
        if not is_latin(google_name):
            stats['skip-latin'] += 1
            continue
        # One decision per Korean name per cycle (avoid double LLM calls / churn).
        if cc_korean_name in seen_kr:
            continue
        seen_kr.add(cc_korean_name)

        cur.execute(
            "SELECT english_name, model FROM name_translations WHERE korean_name = ?",
            (cc_korean_name,),
        )
        existing = cur.fetchone()
        current_en = existing[0] if existing else None
        current_model = existing[1] if existing else None

        # Already this exact Google name from a previous cycle — nothing to do.
        if current_en == google_name and current_model == 'google_native':
            stats['skip-same'] += 1
            continue

        chosen, source = choose_english_name(
            google_name, current_en or "", cc_korean_name, llm_pick=llm_pick)
        stats[source] = stats.get(source, 0) + 1

        if chosen != google_name:
            continue  # classifier kept the existing translation
        if current_en == google_name:
            continue  # value unchanged (just stamp model below isn't worth a write)

        cur.execute("""
            UPDATE name_translations
               SET english_name = ?, model = 'google_native', translated_at = datetime('now')
             WHERE korean_name = ?
        """, (google_name, cc_korean_name))
        count += cur.rowcount
    eng.commit()
    print(f"  google native pre-pass: adopted {count} Google names "
          f"(anchor={stats['anchor']} no-current={stats['no-current']} "
          f"llm-grey={stats['llm']} rejected={stats['romanization-reject']})")
    return count


_KOREAN = re.compile(r'[가-힣]')
_PARENS = re.compile(r'\s*\((?![\w\s]*\)$)[^)]{10,}\)\s*$')  # strip long trailing parentheticals

def _clean_en(en: str) -> str:
    """Strip model commentary, leading 'N. ' leaks, trailing semicolons."""
    en = en.strip()
    en = re.sub(r'^[Nn]\.\s+', '', en)          # "N. Starbucks" → "Starbucks"
    en = en.split(';')[0].strip()               # truncate at semicolon commentary
    en = _PARENS.sub('', en).strip()            # strip long parenthetical notes
    return en

def _valid(kr: str, en: str) -> bool:
    """Reject if empty, Korean chars in output, or untranslated Korean (en==kr and kr has Korean)."""
    if not en:
        return False
    if _KOREAN.search(en):
        return False
    if en == kr and _KOREAN.search(kr):
        return False  # Korean name returned unchanged
    return True

def _parse_numbered(text: str, batch: list) -> dict:
    """Parse 'N. EnglishName' lines → {korean: english}.
    Returns empty dict if count != batch size (index drift detected)."""
    lines = []
    for line in text.splitlines():
        m = re.match(r'^(\d+)[.)]\s*(.+)', line.strip())
        if m:
            lines.append((int(m.group(1)) - 1, m.group(2).strip()))

    if len(lines) != len(batch):
        return {}  # count mismatch → caller retries one-by-one

    result = {}
    for idx, en in lines:
        if 0 <= idx < len(batch):
            cleaned = _clean_en(en)
            if _valid(batch[idx], cleaned):
                result[batch[idx]] = cleaned
    return result


def _translate_one(kr: str, model: str) -> str:
    raw = llm_generate(
        f"Translate this Korean cafe name to English. "
        f"Use transliteration for brand names. "
        f"Return only the English name, nothing else: {kr}",
        max_tokens=30, model=model,
    )
    return _clean_en(raw)


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

    print("Step 2b: google native name pre-pass...")
    google_native_prepass(dbc, eng)

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
