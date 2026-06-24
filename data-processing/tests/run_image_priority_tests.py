#!/usr/bin/env python3
"""
run_image_priority_tests.py — region image-priority baseline (Feature A) test.

Self-contained + re-runnable. Builds a synthetic scraped.db with kakao_scrape_state
rows (some prioritized, some not), starts a dedicated db_server on its own socket,
then exercises the REAL picker + cap functions from
scraper/images/scraper_kakao_images_v3.py and asserts:

  1. The queue picker returns priority>0 cafes BEFORE priority=0 cafes.
  2. Within a priority tier, the lowest-next_page cohort is preferred (breadth-first).
  3. maybe_reset_priority() drops priority to 0 once a cafe has >= image_priority_first_n
     images, so it rejoins the normal (priority=0) queue.
  4. mark_region_image_priority sets priority=1 only on the target region's bbox.

NEVER touches prod scraped.db / clean.db. All work happens in /tmp.
"""
import os
import sys
import time
import signal
import sqlite3
import subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scraper", "lib"))
sys.path.insert(0, os.path.join(ROOT, "scraper", "images"))

PY = os.path.join(ROOT, "venv", "bin", "python3")
SOCK = "/tmp/imgprio_test.sock"
PID = "/tmp/imgprio_test.pid"
DB = "/tmp/imgprio_test_scraped.db"
SERVER_LOG = "/tmp/imgprio_test_db_server.log"

G = "\033[0;32m"; R = "\033[0;31m"; Y = "\033[0;33m"; B = "\033[1m"; NC = "\033[0m"

RESULTS = []


def rec(label, passed, detail=""):
    RESULTS.append((label, passed, detail))
    tag = f"{G}PASS{NC}" if passed else f"{R}FAIL{NC}"
    print(f"  [{tag}] {label}" + (f"  — {detail}" if detail else ""))


# ─── fixture ────────────────────────────────────────────────────────────────

def build_fixture():
    for ext in ("", "-wal", "-shm"):
        try:
            os.remove(DB + ext)
        except FileNotFoundError:
            pass
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE scraped_cafes (
        id TEXT PRIMARY KEY, provider TEXT, provider_id TEXT, name TEXT,
        lat REAL, lon REAL, metadata TEXT)""")
    c.execute("""CREATE TABLE kakao_scrape_state (
        cafe_id TEXT PRIMARY KEY, next_page INTEGER DEFAULT 1,
        attempt_count INTEGER DEFAULT 0, status TEXT DEFAULT 'pending',
        last_attempted TIMESTAMP, priority INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE images (
        id INTEGER PRIMARY KEY AUTOINCREMENT, cafe_id TEXT, provider TEXT,
        photo_id TEXT)""")

    # Cafes:
    #  prio_deep   — priority=1, next_page=8 (deep into its scrape)
    #  prio_fresh  — priority=1, next_page=1 (just started)
    #  normal_low  — priority=0, next_page=1
    #  normal_deep — priority=0, next_page=20
    rows = [
        ("kakao_prio_deep",  "kakao", "1001", "A", 35.10, 129.03),
        ("kakao_prio_fresh", "kakao", "1002", "B", 35.11, 129.04),
        ("kakao_normal_low", "kakao", "1003", "C", 37.49, 126.99),
        ("kakao_normal_deep","kakao", "1004", "D", 37.50, 127.00),
    ]
    c.executemany(
        "INSERT INTO scraped_cafes (id,provider,provider_id,name,lat,lon,metadata) "
        "VALUES (?,?,?,?,?,?,'{}')", rows)
    state = [
        ("kakao_prio_deep",  8,  1),
        ("kakao_prio_fresh", 1,  1),
        ("kakao_normal_low", 1,  0),
        ("kakao_normal_deep",20, 0),
    ]
    c.executemany(
        "INSERT INTO kakao_scrape_state (cafe_id,next_page,priority) VALUES (?,?,?)",
        state)
    c.commit()
    c.close()


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
        [PY, "db_server.py", "--db", DB, "--socket", SOCK,
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


# ─── tests ──────────────────────────────────────────────────────────────────

def run_tests(dbc, scraper):
    print(f"\n{B}── Assertions ──────────────────────────────────────────────{NC}")

    # 1. Picker serves priority rows first. Sample many times (RANDOM ordering
    #    inside a tier) — EVERY pick must be one of the two priority cafes while
    #    any priority cafe remains pending.
    prio_ids = {"kakao_prio_deep", "kakao_prio_fresh"}
    picks = [scraper.pick_random_pending_cafe(dbc)[0] for _ in range(60)]
    all_prio = all(p in prio_ids for p in picks)
    rec("A1: picker returns priority>0 cafes first",
        all_prio, f"distinct picks={sorted(set(picks))}")

    # 2. Within the priority tier, breadth-first prefers the lowest next_page
    #    cohort. prio_fresh (next_page=1) should dominate over prio_deep (page 8).
    fresh = sum(1 for p in picks if p == "kakao_prio_fresh")
    rec("A2: within tier, lowest next_page cohort preferred (breadth-first)",
        fresh == len(picks), f"prio_fresh picks={fresh}/{len(picks)}")

    # 3. Cap: with first_n=30, a cafe with 29 imgs is NOT reset; with 30 it IS.
    first_n = scraper.IMAGE_PRIORITY_FIRST_N
    not_reset = scraper.maybe_reset_priority(dbc, "kakao_prio_deep", 1, first_n - 1)
    p_after_under = dbc.fetchval(
        "SELECT priority FROM kakao_scrape_state WHERE cafe_id='kakao_prio_deep'")
    rec("A3: under-cap cafe keeps priority (not reset)",
        (not not_reset) and p_after_under == 1,
        f"reset={not_reset} priority={p_after_under} (first_n={first_n})")

    did_reset = scraper.maybe_reset_priority(dbc, "kakao_prio_deep", 1, first_n)
    p_after_over = dbc.fetchval(
        "SELECT priority FROM kakao_scrape_state WHERE cafe_id='kakao_prio_deep'")
    rec("A4: at-cap cafe priority reset to 0 (rejoins normal queue)",
        did_reset and p_after_over == 0,
        f"reset={did_reset} priority={p_after_over}")

    # 4. After resetting prio_deep, only prio_fresh remains prioritized; picker
    #    must now return it exclusively.
    picks2 = [scraper.pick_random_pending_cafe(dbc)[0] for _ in range(30)]
    rec("A5: reset cafe drops out of priority tier",
        all(p == "kakao_prio_fresh" for p in picks2),
        f"distinct picks={sorted(set(picks2))}")

    # Reset prio_fresh too → no priority cafes left → picker falls back to the
    # normal (priority=0) queue. prio_fresh (now priority=0, next_page=1) rejoins
    # the lowest-next_page cohort alongside normal_low; the deep one stays last.
    scraper.maybe_reset_priority(dbc, "kakao_prio_fresh", 1, first_n)
    picks3 = [scraper.pick_random_pending_cafe(dbc)[0] for _ in range(40)]
    low_cohort = {"kakao_normal_low", "kakao_prio_fresh"}  # both next_page=1
    rec("A6: with no priority cafes, picker serves the lowest-next_page cohort",
        all(p in low_cohort for p in picks3)
        and "kakao_normal_deep" not in picks3,
        f"distinct picks={sorted(set(picks3))}")


def run_marker_test():
    """mark_region_image_priority sets priority on the right region's bbox only."""
    print(f"\n{B}── Region marker ───────────────────────────────────────────{NC}")
    # Re-mark fresh fixture (all priority=0), then run the marker for 'busan' and
    # assert only the busan-bbox cafes (35.x) get priority=1, not Seoul (37.x).
    c = sqlite3.connect(DB)
    c.execute("UPDATE kakao_scrape_state SET priority=0")
    c.commit(); c.close()

    r = subprocess.run(
        [PY, os.path.join(ROOT, "data-processing", "mark_region_image_priority.py"),
         "--region", "busan", "--socket", SOCK],
        cwd=ROOT, capture_output=True, text=True)
    print(r.stdout.strip())
    if r.returncode != 0:
        rec("A7: marker ran", False, r.stderr.strip()[:200])
        return

    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    busan = c.execute(
        "SELECT COUNT(*) FROM kakao_scrape_state WHERE priority=1 "
        "AND cafe_id IN ('kakao_prio_deep','kakao_prio_fresh')").fetchone()[0]
    seoul = c.execute(
        "SELECT COUNT(*) FROM kakao_scrape_state WHERE priority=1 "
        "AND cafe_id IN ('kakao_normal_low','kakao_normal_deep')").fetchone()[0]
    c.close()
    rec("A7: marker prioritizes only the busan-bbox cafes",
        busan == 2 and seoul == 0, f"busan_prio={busan} seoul_prio={seoul}")


def main():
    print(f"{B}━━━ Image-priority baseline test ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{NC}")
    build_fixture()
    server = None
    try:
        server = start_server()
        from db_client import DBClient
        # import the real scraper module (functions under test)
        import importlib
        scraper = importlib.import_module("scraper_kakao_images_v3")
        dbc = DBClient(socket_path=SOCK)
        run_tests(dbc, scraper)
        run_marker_test()
    finally:
        stop_server()
        for ext in ("", "-wal", "-shm"):
            try:
                os.remove(DB + ext)
            except FileNotFoundError:
                pass

    npass = sum(1 for _, p, _ in RESULTS if p)
    ntot = len(RESULTS)
    print(f"\n{B}━━━ SUMMARY: {npass}/{ntot} passed ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{NC}")
    for label, p, detail in RESULTS:
        if not p:
            print(f"  {R}FAIL{NC} {label} — {detail}")
    sys.exit(0 if npass == ntot else 1)


if __name__ == "__main__":
    main()
