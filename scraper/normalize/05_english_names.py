#!/usr/bin/env python3
"""
05_english_names.py — Batch English name generation for clean_cafes.

Uses qwen2.5:1.5b to translate Korean cafe names to English.
Populates clean_cafes.english_name and cafe_chains.name_english.
Safe to re-run: skips rows where english_name IS NOT NULL.
"""
import os
import sys
import time
import argparse

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..'))

from normalize.cafe_norm_utils import get_english_name
from db_client import DBClient


def progress_bar(done, total, elapsed, bar_len=30):
    pct = done / total if total > 0 else 0
    filled = int(bar_len * pct)
    bar = "█" * filled + "░" * (bar_len - filled)
    eta = ""
    if done > 0 and elapsed > 0:
        rate = done / elapsed
        remaining = (total - done) / rate
        m, s = divmod(int(remaining), 60)
        eta = f"  ETA {m}m{s:02d}s"
    rate_str = f"  {done/elapsed:.1f}/s" if elapsed > 0 else ""
    return f"[{bar}] {done}/{total} ({pct*100:.1f}%) {elapsed:.0f}s{rate_str}{eta}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--chains-only", action="store_true")
    args = parser.parse_args()

    dbc = DBClient()

    print("=== English name generation ===")

    # 1. Chains first (fewer, more important)
    chain_rows = dbc.fetchall(
        "SELECT id, name FROM cafe_chains WHERE name_english IS NULL ORDER BY id"
    )
    print(f"Chains without english name: {len(chain_rows)}")

    done = 0
    start = time.time()
    for cid, name in chain_rows:
        english = get_english_name(name)
        if english:
            dbc.execute("UPDATE cafe_chains SET name_english = ? WHERE id = ?", (english, cid))
        done += 1
        if done % 10 == 0:
            elapsed = time.time() - start
            print(f"\r{progress_bar(done, len(chain_rows), elapsed)}", end="", flush=True)

    elapsed = time.time() - start
    print(f"\nChains done: {done} in {elapsed:.1f}s")

    if args.chains_only:
        return

    # 2. Clean cafes
    q = "SELECT id, name FROM clean_cafes WHERE english_name IS NULL ORDER BY id"
    cafe_rows = dbc.fetchall(q)
    total = len(cafe_rows)
    if args.limit > 0:
        cafe_rows = cafe_rows[:args.limit]
        total = len(cafe_rows)

    print(f"\nClean cafes without english name: {total}")

    done = 0
    start = time.time()
    last_print = 0
    for cid, name in cafe_rows:
        english = get_english_name(name)
        if english:
            dbc.execute("UPDATE clean_cafes SET english_name = ? WHERE id = ?", (english, cid))
        done += 1
        now = time.time()
        if now - last_print >= 2.0:
            print(f"\r{progress_bar(done, total, now - start)}", end="", flush=True)
            last_print = now

    elapsed = time.time() - start
    print(f"\nCafes done: {done} in {elapsed:.1f}s")

    remaining = dbc.fetchval("SELECT COUNT(*) FROM clean_cafes WHERE english_name IS NULL")
    print(f"Still missing: {remaining}")


if __name__ == "__main__":
    main()
