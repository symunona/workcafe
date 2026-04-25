#!/usr/bin/env python3
"""
test_merge.py — Validates merge correctness for a test area around 방배카페거리.

Checks that known same-place cafes from different providers share one clean_cafe.
Each group lists scraped_cafe IDs (stable primary keys) that must end up merged.

Usage: python3 scripts/test_merge.py [--db data/seoul/clean.db]
"""
import sys
import os
import sqlite3
import argparse

# ANSI colors
G  = "\033[0;32m"   # green
R  = "\033[0;31m"   # red
Y  = "\033[0;33m"   # yellow
B  = "\033[1m"      # bold
DIM = "\033[2m"     # dim
NC = "\033[0m"      # reset

def ok(s):   return f"{G}{B}{s}{NC}"
def err(s):  return f"{R}{B}{s}{NC}"
def warn(s): return f"{Y}{s}{NC}"
def dim(s):  return f"{DIM}{s}{NC}"
def bold(s): return f"{B}{s}{NC}"

# ── Test groups ────────────────────────────────────────────────────────────────
# (name, [scraped_cafe_id, ...])
# IDs are scraped_cafes.id — stable across pipeline runs.

GROUPS = [
    ("Starbucks 방배카페거리점", [
        "kakao_27346318",
        "naver_37411185",
        "google_0x357ca1a4b27c7f01:0x67c8d0f1dd3bb2f5",
    ], True),   # True = reference case (was always passing)
    ("Compose Coffee 방배카페거리", [
        "kakao_2092556096",
        "google_0x357ca120de8081c9:0xc47d63101452d4e1",
        "osm_11527371269",
    ], False),
    ("Starbucks 방배점", [
        "kakao_27320715",
        "naver_37373211",
        "osm_5339942321",
    ], False),
    ("Mega Coffee 함지박사거리점", [
        "kakao_1307365391",
        "osm_10065223778",
    ], False),
    ("Starbucks 내방역점", [
        "kakao_24167871",
        "osm_5489003022",
    ], False),
    ("Dessert 39 방배서래점", [
        "kakao_613253983",
        "osm_11527333493",
    ], False),
]


def run_tests(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    passed = failed = skipped = 0

    for group_name, scraped_ids, is_reference in GROUPS:
        rows = conn.execute(
            f"SELECT id, name, provider, belongs_to_cafe_id FROM scraped_cafes "
            f"WHERE id IN ({','.join('?'*len(scraped_ids))})",
            scraped_ids
        ).fetchall()

        found = {r["id"]: r for r in rows}
        missing = [sid for sid in scraped_ids if sid not in found]

        clean_ids = set(r["belongs_to_cafe_id"] for r in rows if r["belongs_to_cafe_id"])
        none_count = sum(1 for r in rows if not r["belongs_to_cafe_id"])

        ref_label = dim("  [reference]") if is_reference else ""

        if missing:
            skipped += 1
            print(f"  {warn('SKIP')}  {group_name}{ref_label}")
            print(dim(f"         not in DB: {missing}"))
            continue

        if len(clean_ids) == 1 and none_count == 0:
            cid = next(iter(clean_ids))
            cname = conn.execute("SELECT name FROM clean_cafes WHERE id=?", (cid,)).fetchone()
            label = cname["name"] if cname else cid
            prov_list = ", ".join(sorted(set(r["provider"] for r in rows)))
            print(f"  {ok('PASS')}  {bold(group_name)}{ref_label}")
            print(dim(f"         [{prov_list}] → {label}"))
            passed += 1
        else:
            failed += 1
            print(f"  {err('FAIL')}  {bold(group_name)}{ref_label}")
            for r in rows:
                cc = conn.execute(
                    "SELECT name FROM clean_cafes WHERE id=?", (r["belongs_to_cafe_id"],)
                ).fetchone() if r["belongs_to_cafe_id"] else None
                cc_label = cc["name"] if cc else err("(unmerged)")
                prov_col = f"{r['provider']:8}"
                name_col = f"{r['name'][:32]:32}"
                print(f"         {dim(prov_col)} {name_col}  →  {cc_label}")
            if len(clean_ids) > 1:
                print(warn(f"         ↳ {len(clean_ids)} separate clean_cafes — expected 1"))
            if none_count:
                print(warn(f"         ↳ {none_count} unmerged (belongs_to_cafe_id IS NULL)"))

    print()
    total = passed + failed
    if failed == 0:
        print(ok(f"  ✓  {passed}/{total} passed") + (f"  {dim(f'({skipped} skipped)')}" if skipped else ""))
    else:
        print(err(f"  ✗  {passed}/{total} passed, {failed} failed") + (f"  {dim(f'({skipped} skipped)')}" if skipped else ""))

    conn.close()
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "seoul", "clean.db"
    ))
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(err(f"DB not found: {args.db}"), file=sys.stderr)
        sys.exit(1)

    print(f"\n{bold('Merge quality — 방배/내방 test area')}  {dim(args.db)}\n")
    sys.exit(run_tests(args.db))
