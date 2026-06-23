#!/usr/bin/env python3
"""
run_merger_tests.py — EXTENSIVE post-merge DB correctness test for the cafe merger.

Self-contained + re-runnable + idempotent. Builds a synthetic scraped DB
(via build_merger_fixture), runs the real merge pipeline steps against it on a
dedicated db_server socket, then asserts each designed case with SQL and prints
PASS/FAIL. Cleans up its own socket + pidfile.

NEVER touches prod scraped.db / clean.db. All work happens in /tmp.

Steps:
  1. build synthetic scraped.db          (build_merger_fixture)
  2. cp → clean.db                       (pipeline operates on a copy)
  3. start dedicated db_server           (--socket /tmp/merger_test.sock)
  4. 01_migrate_db                       (add clean_cafes / cafe_chains / cols)
  5. 03_detect_chains
  6. seed englishify cache for case 4c   (synthetic translation rows)
  7. 04_normalize_pipeline (THE MERGER)  (--no-backup, no --embed)
  8. 06_update_image_links               (link images post-merge)
  9. SQL assertions per case             (PASS/FAIL)
 10. idempotency: run 7+8 again, assert counts stable
 11. cleanup
"""
import os
import sys
import time
import json
import signal
import sqlite3
import argparse
import subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _HERE)

from build_merger_fixture import build, build_cases  # noqa: E402

PY = os.path.join(ROOT, "venv", "bin", "python3")
SOCK = "/tmp/merger_test.sock"
PID = "/tmp/merger_test.pid"
SCRAPED_DB = "/tmp/merger_test_scraped.db"
CLEAN_DB = "/tmp/merger_test_clean.db"
ENG_DB = "/tmp/merger_test_englishify.db"
SERVER_LOG = "/tmp/merger_test_db_server.log"

G = "\033[0;32m"; R = "\033[0;31m"; Y = "\033[0;33m"; B = "\033[1m"; NC = "\033[0m"

RESULTS = []  # (case_label, passed, detail)


def rec(label, passed, detail=""):
    RESULTS.append((label, passed, detail))
    tag = f"{G}PASS{NC}" if passed else f"{R}FAIL{NC}"
    print(f"  [{tag}] {label}" + (f"  — {detail}" if detail else ""))


def run(cmd, **kw):
    print(f"{Y}$ {' '.join(str(c) for c in cmd)}{NC}")
    return subprocess.run(cmd, **kw)


# ─── db_server lifecycle ────────────────────────────────────────────────────

def stop_server():
    if os.path.exists(PID):
        try:
            old = open(PID).read().strip()
            if old:
                os.kill(int(old), signal.SIGTERM)
                time.sleep(0.5)
        except Exception:
            pass
    for p in (PID, SOCK):
        try:
            os.remove(p)
        except FileNotFoundError:
            pass


def start_server():
    stop_server()
    proc = subprocess.Popen(
        [PY, "db_server.py", "--db", CLEAN_DB, "--socket", SOCK,
         "--pid-file", PID, "--replace", "--unsafe-any-db"],
        cwd=os.path.join(ROOT, "scraper"),
        stdout=open(SERVER_LOG, "w"), stderr=subprocess.STDOUT,
    )
    for _ in range(40):
        if os.path.exists(SOCK):
            break
        time.sleep(0.3)
    if not os.path.exists(SOCK):
        raise RuntimeError(f"db_server did not start; see {SERVER_LOG}")
    return proc


# ─── englishify seed (case 4c) ──────────────────────────────────────────────

def seed_englishify():
    if os.path.exists(ENG_DB):
        os.remove(ENG_DB)
    c = sqlite3.connect(ENG_DB)
    c.execute("""CREATE TABLE name_translations (
        korean_name TEXT PRIMARY KEY, english_name TEXT,
        model TEXT, translated_at TEXT)""")
    # Case 4c: give the Korean+Latin pair matching english names so soft-zone
    # english_name comparison succeeds. Case 4b deliberately gets NO entry.
    rows = [
        ("프릳츠 도화", "Fritz Dohwa", "test"),
        ("Fritz Dohwa", "Fritz Dohwa", "test"),
    ]
    c.executemany(
        "INSERT INTO name_translations (korean_name, english_name, model, translated_at) "
        "VALUES (?,?,?,datetime('now'))", rows)
    c.commit(); c.close()
    print(f"Seeded englishify cache {ENG_DB} ({len(rows)} rows)")


# ─── pipeline ───────────────────────────────────────────────────────────────

def reconcile_clean_schema():
    """Add clean_cafes columns that exist in prod clean.db but are NOT created by
    01_migrate_db.py (metadata, tags, has_custom_website, custom_website_url).
    The normalizer's INSERT references `metadata`, so without these the merge
    errors out. This mirrors the real prod clean.db schema. (See report:
    01_migrate_db.py is out of sync with the live clean_cafes schema.)"""
    c = sqlite3.connect(CLEAN_DB, timeout=30)
    have = {r[1] for r in c.execute("PRAGMA table_info(clean_cafes)")}
    for col, typ in [("metadata", "TEXT"), ("tags", "TEXT"),
                     ("has_custom_website", "INTEGER DEFAULT 0"),
                     ("custom_website_url", "TEXT")]:
        if col not in have:
            c.execute(f"ALTER TABLE clean_cafes ADD COLUMN {col} {typ}")
    c.commit(); c.close()


def run_pipeline(first=True):
    if first:
        run([PY, os.path.join(ROOT, "data-processing", "03_detect_chains.py"),
             "--socket", SOCK], check=True)
    run([PY, os.path.join(ROOT, "data-processing", "04_normalize_pipeline.py"),
         "--db", CLEAN_DB, "--socket", SOCK, "--englishify-db", ENG_DB,
         "--no-backup"], check=True)
    run([PY, os.path.join(ROOT, "data-processing", "06_update_image_links.py"),
         "--socket", SOCK], check=True)


# ─── assertion helpers ──────────────────────────────────────────────────────

def q(conn, sql, params=()):
    return conn.execute(sql, params).fetchall()


def clean_id_of(conn, scraped_id):
    r = conn.execute("SELECT belongs_to_cafe_id FROM scraped_cafes WHERE id=?",
                     (scraped_id,)).fetchone()
    return r[0] if r else None


def clean_row(conn, clean_id):
    r = conn.execute(
        "SELECT providers, source_ids FROM clean_cafes WHERE id=?",
        (clean_id,)).fetchone()
    if not r:
        return None, None
    return json.loads(r[0] or "[]"), json.loads(r[1] or "[]")


# ─── per-case assertions ────────────────────────────────────────────────────

def assert_cases(conn):
    print(f"\n{B}── Assertions ──────────────────────────────────────────────{NC}")

    # Case 1: same provider ~5m → NOT merge → 2 distinct clean_cafes
    a = clean_id_of(conn, "kakao_c1a")
    b = clean_id_of(conn, "kakao_c1b")
    rec("Case 1: same-provider guard (no merge)",
        a is not None and b is not None and a != b,
        f"clean ids a={a} b={b}")

    # Case 2: kakao+google ~5m similar → MERGE → 1 clean, both providers/ids
    k = clean_id_of(conn, "kakao_c2")
    g = clean_id_of(conn, "google_c2")
    merged2 = k is not None and k == g
    provs, sids = clean_row(conn, k) if k else (None, None)
    rec("Case 2: cross-provider merge (1 clean)",
        merged2 and set(provs or []) == {"kakao", "google"}
        and {"kakao_c2", "google_c2"}.issubset(set(sids or [])),
        f"clean={k} providers={provs} source_ids={sids}")

    # Case 3: google fed first, kakao must merge in → 1 clean
    g3 = clean_id_of(conn, "google_c3")
    k3 = clean_id_of(conn, "kakao_c3")
    merged3 = g3 is not None and g3 == k3
    provs3, sids3 = clean_row(conn, g3) if g3 else (None, None)
    rec("Case 3: order-independence (google-first, 1 clean)",
        merged3 and set(provs3 or []) == {"kakao", "google"},
        f"clean={g3} providers={provs3} source_ids={sids3}")

    # Case 4a: cross-language ~5m hard zone → MERGE
    k4a = clean_id_of(conn, "kakao_c4a")
    g4a = clean_id_of(conn, "google_c4a")
    rec("Case 4a: cross-language hard-zone merge (1 clean)",
        k4a is not None and k4a == g4a,
        f"kakao={k4a} google={g4a}")

    # Case 4b: cross-language ~15m, NO englishify → NOT merge (documents limit)
    k4b = clean_id_of(conn, "kakao_c4b")
    g4b = clean_id_of(conn, "google_c4b")
    rec("Case 4b: cross-language soft-zone, no-eng (NO merge, expected limit)",
        k4b is not None and g4b is not None and k4b != g4b,
        f"kakao={k4b} google={g4b}")

    # Case 4c: cross-language ~15m, WITH englishify → MERGE via english_name
    k4c = clean_id_of(conn, "kakao_c4c")
    g4c = clean_id_of(conn, "google_c4c")
    rec("Case 4c: cross-language soft-zone, eng-cache (MERGE via english_name)",
        k4c is not None and k4c == g4c,
        f"kakao={k4c} google={g4c}")

    # Case 5: genuinely different ~150m → NOT merge → 2 clean
    a5 = clean_id_of(conn, "kakao_c5")
    b5 = clean_id_of(conn, "google_c5")
    rec("Case 5: different cafes 150m (no merge)",
        a5 is not None and b5 is not None and a5 != b5,
        f"a={a5} b={b5}")

    # ── Case 6: IMAGES ───────────────────────────────────────────────────────
    cases = build_cases()
    img_total_expected = sum(c["n_images"] for c in cases)
    img_total = conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
    rec("Case 6.0: image count preserved (no dup / no loss)",
        img_total == img_total_expected,
        f"expected={img_total_expected} actual={img_total}")

    # every image whose parent scraped_cafe got a clean id must be linked to it
    bad_link = conn.execute("""
        SELECT COUNT(*) FROM images i
        JOIN scraped_cafes s ON s.id = i.cafe_id
        WHERE s.belongs_to_cafe_id IS NOT NULL
          AND (i.belongs_to_cafe_id IS NULL
               OR i.belongs_to_cafe_id != s.belongs_to_cafe_id)
    """).fetchone()[0]
    rec("Case 6.1: every image links to its parent's clean_cafe",
        bad_link == 0, f"mismatched/orphan images={bad_link}")

    # no image points at a clean_cafe id that doesn't exist
    dangling = conn.execute("""
        SELECT COUNT(*) FROM images i
        WHERE i.belongs_to_cafe_id IS NOT NULL
          AND i.belongs_to_cafe_id NOT IN (SELECT id FROM clean_cafes)
    """).fetchone()[0]
    rec("Case 6.2: no image links to a non-existent clean_cafe",
        dangling == 0, f"dangling links={dangling}")

    # specifically: merged cases — all images of both sources share one clean id
    for label, sa, sb in [("Case 6.3 merged (case2)", "kakao_c2", "google_c2"),
                          ("Case 6.4 merged (case3)", "google_c3", "kakao_c3"),
                          ("Case 6.5 merged (case4a)", "kakao_c4a", "google_c4a")]:
        ca = clean_id_of(conn, sa)
        imgs = conn.execute(
            "SELECT DISTINCT belongs_to_cafe_id FROM images WHERE cafe_id IN (?,?)",
            (sa, sb)).fetchall()
        ok = ca is not None and len(imgs) == 1 and imgs[0][0] == ca
        rec(label + ": all images on one clean id", ok,
            f"clean={ca} distinct_img_links={[i[0] for i in imgs]}")

    # no orphan images for a merged parent (belongs NULL though parent linked)
    orphan = conn.execute("""
        SELECT COUNT(*) FROM images i
        JOIN scraped_cafes s ON s.id = i.cafe_id
        WHERE s.belongs_to_cafe_id IS NOT NULL AND i.belongs_to_cafe_id IS NULL
    """).fetchone()[0]
    rec("Case 6.6: no orphan images under a linked parent",
        orphan == 0, f"orphans={orphan}")

    return {
        "clean_cafes": conn.execute("SELECT COUNT(*) FROM clean_cafes").fetchone()[0],
        "linked_images": conn.execute(
            "SELECT COUNT(*) FROM images WHERE belongs_to_cafe_id IS NOT NULL").fetchone()[0],
        "linked_scraped": conn.execute(
            "SELECT COUNT(*) FROM scraped_cafes WHERE belongs_to_cafe_id IS NOT NULL").fetchone()[0],
    }


def open_clean():
    # read fresh state; checkpoint so direct reader sees socket-server writes
    c = sqlite3.connect(CLEAN_DB, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="keep DBs+socket after run")
    args = ap.parse_args()

    print(f"{B}━━━ Merger correctness test ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{NC}")

    # 1. fixture
    build(SCRAPED_DB)
    # 2. copy to clean db (pipeline mutates a copy)
    import shutil
    for ext in ("", "-wal", "-shm"):
        try:
            os.remove(CLEAN_DB + ext)
        except FileNotFoundError:
            pass
    shutil.copyfile(SCRAPED_DB, CLEAN_DB)
    # 6. englishify seed
    seed_englishify()

    # 4. migrate + reconcile schema BEFORE starting the server (direct sqlite3)
    run([PY, os.path.join(ROOT, "data-processing", "01_migrate_db.py"),
         "--db", CLEAN_DB], check=True)
    reconcile_clean_schema()

    server = None
    try:
        # 3. server
        server = start_server()
        # 4-8. first pipeline pass
        print(f"\n{B}── Pipeline pass 1 ─────────────────────────────────────────{NC}")
        run_pipeline(first=True)

        conn = open_clean()
        snap1 = assert_cases(conn)
        conn.close()

        # 10. idempotency — rerun normalize + link
        print(f"\n{B}── Pipeline pass 2 (idempotency) ───────────────────────────{NC}")
        run_pipeline(first=False)
        conn = open_clean()
        snap2 = {
            "clean_cafes": conn.execute("SELECT COUNT(*) FROM clean_cafes").fetchone()[0],
            "linked_images": conn.execute(
                "SELECT COUNT(*) FROM images WHERE belongs_to_cafe_id IS NOT NULL").fetchone()[0],
            "linked_scraped": conn.execute(
                "SELECT COUNT(*) FROM scraped_cafes WHERE belongs_to_cafe_id IS NOT NULL").fetchone()[0],
        }
        # also check no source_id duplicated inside any clean_cafe
        dup_sid = 0
        for (sids_json,) in conn.execute("SELECT source_ids FROM clean_cafes"):
            sids = json.loads(sids_json or "[]")
            if len(sids) != len(set(sids)):
                dup_sid += 1
        conn.close()

        print(f"\n{B}── Idempotency ─────────────────────────────────────────────{NC}")
        rec("Case 7.0: clean_cafes count stable after rerun",
            snap1["clean_cafes"] == snap2["clean_cafes"],
            f"pass1={snap1['clean_cafes']} pass2={snap2['clean_cafes']}")
        rec("Case 7.1: linked image count stable after rerun",
            snap1["linked_images"] == snap2["linked_images"],
            f"pass1={snap1['linked_images']} pass2={snap2['linked_images']}")
        rec("Case 7.2: no duplicate source_ids inside any clean_cafe",
            dup_sid == 0, f"clean_cafes with dup source_ids={dup_sid}")

    finally:
        stop_server()
        if not args.keep:
            for f in (SCRAPED_DB, CLEAN_DB, ENG_DB):
                for ext in ("", "-wal", "-shm"):
                    try:
                        os.remove(f + ext)
                    except FileNotFoundError:
                        pass
            try:
                os.remove(CLEAN_DB + ".pipeline.lock")
            except FileNotFoundError:
                pass

    # summary
    npass = sum(1 for _, p, _ in RESULTS if p)
    ntot = len(RESULTS)
    print(f"\n{B}━━━ SUMMARY: {npass}/{ntot} passed ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{NC}")
    for label, p, detail in RESULTS:
        if not p:
            print(f"  {R}FAIL{NC} {label} — {detail}")
    sys.exit(0 if npass == ntot else 1)


if __name__ == "__main__":
    main()
