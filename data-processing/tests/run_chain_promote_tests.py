#!/usr/bin/env python3
"""
run_chain_promote_tests.py — on-the-fly chain promotion (Feature B) test.

Self-contained + re-runnable. Builds a tiny synthetic clean.db, starts a
dedicated db_server, then drives the REAL 04_normalize.process_cafe path to
assert the incremental (no global rescan) chain handling:

  B1. A KNOWN chain (Starbucks) is assigned a chain_id on the first cafe.
  B2. A NOVEL brand stays chain-less below chain_promote_min cafes...
  B3. ...and is PROMOTED to a chain (and assigned) exactly at chain_promote_min.
  B4. Branch variants of the promoted brand share the SAME chain_id.
  B5. A generic token ("카페") is never promoted, regardless of count.

NEVER touches prod scraped.db / clean.db. All work happens in /tmp.
"""
import os
import sys
import time
import signal
import sqlite3
import importlib
import subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scraper", "lib"))
sys.path.insert(0, os.path.join(ROOT, "data-processing"))

PY = os.path.join(ROOT, "venv", "bin", "python3")
SOCK = "/tmp/chainpromote_test.sock"
PID = "/tmp/chainpromote_test.pid"
DB = "/tmp/chainpromote_test_clean.db"
SERVER_LOG = "/tmp/chainpromote_test_db_server.log"

G = "\033[0;32m"; R = "\033[0;31m"; B = "\033[1m"; NC = "\033[0m"
RESULTS = []


def rec(label, passed, detail=""):
    RESULTS.append((label, passed, detail))
    tag = f"{G}PASS{NC}" if passed else f"{R}FAIL{NC}"
    print(f"  [{tag}] {label}" + (f"  — {detail}" if detail else ""))


def build_db():
    for ext in ("", "-wal", "-shm"):
        try:
            os.remove(DB + ext)
        except FileNotFoundError:
            pass
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE clean_cafes (
        id TEXT PRIMARY KEY, chain_id TEXT, name TEXT, english_name TEXT,
        avg_lat REAL, avg_lon REAL, address TEXT, url TEXT, providers TEXT,
        source_ids TEXT, name_embedding BLOB, tags TEXT, metadata TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE cafe_chains (
        id TEXT PRIMARY KEY, name TEXT, name_english TEXT, logo TEXT,
        name_embed BLOB, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    c.commit(); c.close()


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


_lat = 35.0
def _cafe(cid, name, provider="kakao"):
    """Distinct far-apart coords so spatial merge never fires (chain path only)."""
    global _lat
    _lat += 0.05
    return {"id": cid, "name": name, "lat": _lat, "lon": 129.0,
            "provider": provider, "address": "", "url": "", "metadata": ""}


def main():
    print(f"{B}━━━ On-the-fly chain promotion test ━━━━━━━━━━━━━━━━━━━━━━━━━━{NC}")
    build_db()
    server = None
    try:
        server = start_server()
        from db_client import DBClient
        norm = importlib.import_module("04_normalize_pipeline")
        dbc = DBClient(socket_path=SOCK)
        conn = sqlite3.connect(DB, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")

        norm.CHAIN_PROMOTE_MIN = 5  # force a small, deterministic threshold
        chain_names: list = norm.load_chain_cache(conn)

        def run(cafe):
            cid, _, _ = norm.process_cafe(conn, dbc, cafe, chain_names,
                                          embed_enabled=False, eng_lookup={})
            # mirror the main loop's belongs link so re-reads are consistent
            dbc.execute(
                "UPDATE clean_cafes SET id=id WHERE id=?", (cid,))
            return cid

        def chain_of(clean_id):
            return dbc.fetchval(
                "SELECT chain_id FROM clean_cafes WHERE id=?", (clean_id,))

        print(f"\n{B}── Assertions (chain_promote_min={norm.CHAIN_PROMOTE_MIN}) ──{NC}")

        # B1: known chain assigned immediately
        sb = run(_cafe("sb1", "스타벅스 강남점"))
        rec("B1: known chain (Starbucks) assigned on first cafe",
            chain_of(sb) is not None, f"chain_id={chain_of(sb)}")

        # B2/B3: novel brand '두유노프레소' promoted exactly at the threshold.
        novel_ids = []
        for i in range(norm.CHAIN_PROMOTE_MIN):
            novel_ids.append(run(_cafe(f"nv{i}", f"두유노프레소 {i}호점")))
        below = [chain_of(novel_ids[i]) for i in range(norm.CHAIN_PROMOTE_MIN - 1)]
        at = chain_of(novel_ids[-1])
        rec("B2: novel brand chain-less below threshold",
            all(c is None for c in below), f"chains below={below}")
        rec("B3: novel brand promoted+assigned AT threshold",
            at is not None, f"chain_id@{norm.CHAIN_PROMOTE_MIN}={at}")

        # B4: the next variant of the promoted brand reuses the same chain_id
        nxt = run(_cafe("nv_next", "두유노프레소 신규점"))
        rec("B4: post-promotion variants share one chain_id",
            chain_of(nxt) is not None and chain_of(nxt) == at,
            f"next={chain_of(nxt)} promoted={at}")
        n_chain = dbc.fetchval(
            "SELECT COUNT(*) FROM clean_cafes WHERE chain_id=?", (at,))
        rec("B4b: promoted chain has the expected member count",
            n_chain >= 2, f"members with promoted chain_id={n_chain}")

        # B5: a generic token is never promoted no matter how many
        generic = [run(_cafe(f"g{i}", "카페")) for i in range(norm.CHAIN_PROMOTE_MIN + 3)]
        rec("B5: generic token ('카페') never promoted",
            all(chain_of(g) is None for g in generic),
            f"non-null chains={sum(1 for g in generic if chain_of(g))}")

        conn.close()
    finally:
        stop_server()
        for ext in ("", "-wal", "-shm"):
            try:
                os.remove(DB + ext)
            except FileNotFoundError:
                pass
        try:
            os.remove(DB + ".pipeline.lock")
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
