#!/usr/bin/env python3
"""
04_normalize_pipeline.py — Main cafe normalization pipeline.

Reads: direct SQLite (WAL — concurrent readers OK with running scraper).
Writes: db_client socket → db_server (serialized, no locking conflict).

Flow per cafe:
1. Find existing clean_cafe within 50m (read)
2. < 20m: always merge; 20-50m: merge if lev-sim > 0.4
3. No match: detect chain, create new clean_cafe
4. Store embedding blob (for debugging, --embed flag)

English names handled separately in 05_english_names.py for speed.
Safe to restart: only processes cafes with belongs_to_cafe_id IS NULL.
"""
import os
import sys
import json
import uuid
import sqlite3
import time
import argparse
from typing import Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..'))

from normalize.cafe_norm_utils import (
    haversine_m, name_similarity, get_embedding, embed_to_blob,
    is_chain_cafe, lat_lon_bbox, get_english_name
)
from db_client import DBClient

DB_PATH = os.path.abspath(os.path.join(_HERE, '..', '..', 'data', 'seoul', 'cafedata.db'))

MERGE_RADIUS_AUTO = 30.0
MERGE_RADIUS_MAX = 150.0
NAME_SIM_THRESHOLD = 0.4
BRANCH_SUFFIXES = ["DT점", "DT", "역점", "공항점", "역사점", "터미널점", "점"]


def strip_branch_suffix(name: str) -> str:
    for suf in BRANCH_SUFFIXES:
        if name.endswith(suf):
            return name[:-len(suf)].strip()
    parts = name.split()
    if len(parts) > 1 and any(parts[-1].endswith(s) for s in ["점", "DT"]):
        return " ".join(parts[:-1])
    return name


# ─── Read helpers (direct SQLite) ─────────────────────────────────────────────

def find_clean_cafes_nearby(conn, lat, lon, radius_m=50.0):
    min_lat, max_lat, min_lon, max_lon = lat_lon_bbox(lat, lon, radius_m)
    rows = conn.execute("""
        SELECT id, name, avg_lat, avg_lon, providers, source_ids, chain_id
        FROM clean_cafes
        WHERE avg_lat BETWEEN ? AND ? AND avg_lon BETWEEN ? AND ?
    """, (min_lat, max_lat, min_lon, max_lon)).fetchall()
    result = []
    for row in rows:
        dist = haversine_m(lat, lon, row[2], row[3])
        if dist <= radius_m:
            result.append({
                "id": row[0], "name": row[1],
                "avg_lat": row[2], "avg_lon": row[3],
                "providers": row[4], "source_ids": row[5],
                "chain_id": row[6], "distance_m": dist,
            })
    return sorted(result, key=lambda x: x["distance_m"])


# ─── Chain management (in-memory cache avoids stale-read race) ───────────────

_chain_id_cache: dict[str, str] = {}   # base_name -> chain_id


def get_or_create_chain(conn, dbc: DBClient, chain_name: str) -> str:
    if chain_name in _chain_id_cache:
        return _chain_id_cache[chain_name]

    # Check DB via direct read (may lag slightly, but cache handles in-process duplicates)
    row = conn.execute("SELECT id FROM cafe_chains WHERE name = ?", (chain_name,)).fetchone()
    if row:
        _chain_id_cache[chain_name] = row[0]
        return row[0]

    # Check approximate name match in DB
    existing = conn.execute("SELECT id, name FROM cafe_chains").fetchall()
    for cid, cname in existing:
        if name_similarity(chain_name, cname)["combined"] > 0.85:
            _chain_id_cache[chain_name] = cid
            return cid

    chain_id = str(uuid.uuid4())
    dbc.execute(
        "INSERT INTO cafe_chains (id, name) VALUES (?, ?)",
        (chain_id, chain_name)
    )
    _chain_id_cache[chain_name] = chain_id
    return chain_id


def load_chain_cache(conn):
    rows = conn.execute("SELECT id, name FROM cafe_chains").fetchall()
    for cid, cname in rows:
        _chain_id_cache[cname] = cid
    return list(_chain_id_cache.keys())


# ─── Clean cafe operations ─────────────────────────────────────────────────────

def extract_best_address(cafe: dict) -> str:
    best_addr = cafe.get("address") or ""
    if cafe.get("metadata"):
        try:
            meta = json.loads(cafe["metadata"])
            if cafe["provider"] == "naver":
                best_addr = meta.get("roadAddress") or meta.get("address") or best_addr
            elif cafe["provider"] == "kakao":
                best_addr = meta.get("new_address") or meta.get("address") or best_addr
        except:
            pass
    return best_addr.strip() if best_addr else ""

def create_clean_cafe(dbc: DBClient, cafe: dict, chain_id=None, emb_blob=None) -> str:
    clean_id = str(uuid.uuid4())
    
    metadata = {}
    if cafe.get("metadata"):
        try:
            metadata[cafe["provider"]] = json.loads(cafe["metadata"])
        except:
            pass

    best_addr = extract_best_address(cafe)

    dbc.execute("""
        INSERT INTO clean_cafes
            (id, chain_id, name, avg_lat, avg_lon,
             address, url, providers, source_ids, name_embedding, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        clean_id, chain_id, cafe["name"],
        cafe["lat"], cafe["lon"],
        best_addr, cafe.get("url", ""),
        json.dumps([cafe["provider"]]), json.dumps([cafe["id"]]),
        emb_blob, json.dumps(metadata)
    ))
    return clean_id

def merge_into_clean_cafe(conn, dbc: DBClient, clean_id: str, cafe: dict):
    row = conn.execute(
        "SELECT avg_lat, avg_lon, providers, source_ids, address, url, metadata FROM clean_cafes WHERE id = ?",
        (clean_id,)
    ).fetchone()
    if not row:
        return

    old_lat, old_lon = row[0], row[1]
    providers = json.loads(row[2] or "[]")
    source_ids = json.loads(row[3] or "[]")
    address = row[4] or ""
    url = row[5] or ""
    
    metadata = {}
    if row[6]:
        try:
            metadata = json.loads(row[6])
        except:
            pass

    if cafe["id"] in source_ids:
        return  # already merged

    providers_set = set(providers)
    providers_set.add(cafe["provider"])
    source_ids.append(cafe["id"])
    n = len(source_ids)
    avg_lat = (old_lat * (n - 1) + cafe["lat"]) / n
    avg_lon = (old_lon * (n - 1) + cafe["lon"]) / n

    if not address:
        address = extract_best_address(cafe)
    if not url and cafe.get("url"):
        url = cafe["url"]
        
    if cafe.get("metadata"):
        try:
            metadata[cafe["provider"]] = json.loads(cafe["metadata"])
        except:
            pass

    dbc.execute("""
        UPDATE clean_cafes
        SET providers = ?, source_ids = ?, avg_lat = ?, avg_lon = ?,
            address = ?, url = ?, metadata = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (json.dumps(sorted(providers_set)), json.dumps(source_ids),
          avg_lat, avg_lon, address, url, json.dumps(metadata), clean_id))


# ─── Core: process one cafe ────────────────────────────────────────────────────

def process_cafe(conn, dbc: DBClient, cafe: dict, chain_names: list,
                  embed_enabled: bool) -> tuple[str, bool]:
    """
    Returns (clean_cafe_id, was_created).
    was_created=True if new clean_cafe row created, False if merged into existing.
    Note: caller is responsible for updating cafes.belongs_to_cafe_id (batched).
    """
    lat, lon = cafe["lat"], cafe["lon"]

    emb_blob = None
    if embed_enabled:
        from normalize.cafe_norm_utils import get_embedding, embed_to_blob
        emb_vec = get_embedding(cafe["name"])
        if emb_vec is not None:
            emb_blob = embed_to_blob(emb_vec)

    conn.commit()
    nearby = find_clean_cafes_nearby(conn, lat, lon, MERGE_RADIUS_MAX)

    matched_id = None
    for nc in nearby:
        dist = nc["distance_m"]

        if dist <= MERGE_RADIUS_AUTO:
            matched_id = nc["id"]
            break

        sim = name_similarity(cafe["name"], nc["name"])
        if sim["combined"] >= NAME_SIM_THRESHOLD:
            matched_id = nc["id"]
            break

        # Chain same-name check
        if nc["chain_id"]:
            cr = is_chain_cafe(cafe["name"], chain_names)
            if cr["is_chain"]:
                chain_base = cr.get("chain_name") or strip_branch_suffix(cafe["name"])
                row = conn.execute(
                    "SELECT name FROM cafe_chains WHERE id = ?", (nc["chain_id"],)
                ).fetchone()
                if row and name_similarity(chain_base, row[0])["combined"] >= 0.8:
                    matched_id = nc["id"]
                    break

    if matched_id:
        merge_into_clean_cafe(conn, dbc, matched_id, cafe)
        # belongs_to_cafe_id update deferred to batch (see main loop)
        return matched_id, False

    # Create new
    chain_id = None
    cr = is_chain_cafe(cafe["name"], chain_names)
    if cr["is_chain"]:
        base = cr.get("chain_name") or strip_branch_suffix(cafe["name"])
        chain_id = get_or_create_chain(conn, dbc, base)
        if base not in chain_names:
            chain_names.append(base)

    clean_id = create_clean_cafe(dbc, cafe, chain_id, emb_blob)
    # belongs_to_cafe_id update deferred to batch (see main loop)
    return clean_id, True


# ─── Progress ─────────────────────────────────────────────────────────────────

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


# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--embed", action="store_true", help="Generate embeddings (slow)")
    parser.add_argument("--provider", help="Only process this provider")
    parser.add_argument("--reset", action="store_true", help="Reset clean data completely before running")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA cache_size=10000")
    dbc = DBClient()

    if args.reset:
        print("WARNING: Resetting all clean data...")
        conn.execute("DELETE FROM clean_cafes")
        conn.execute("DELETE FROM cafe_chains")
        conn.execute("UPDATE cafes SET belongs_to_cafe_id = NULL")
        conn.execute("UPDATE images SET belongs_to_cafe_id = NULL")
        conn.commit()
        print("Clean data reset.")

    chain_names = load_chain_cache(conn)
    print(f"Chains loaded: {len(chain_names)}")

    q = "SELECT COUNT(*) FROM cafes WHERE belongs_to_cafe_id IS NULL"
    params = []
    if args.provider:
        q += " AND provider = ?"
        params.append(args.provider)
    total = conn.execute(q, params).fetchone()[0]
    if args.limit > 0:
        total = min(total, args.limit)
    print(f"Cafes to process: {total}")

    fetch_q = ("SELECT id, name, lat, lon, provider, address, url, metadata FROM cafes "
               "WHERE belongs_to_cafe_id IS NULL")
    if args.provider:
        fetch_q += " AND provider = ?"
    fetch_q += " ORDER BY provider, id"

    cursor = conn.execute(fetch_q, params)
    done = created = merged = errors = 0
    start = time.time()
    last_print = 0

    print("Running... (Ctrl+C to pause, safe to restart)")

    try:
        while True:
            rows = cursor.fetchmany(500)
            if not rows:
                break
            if args.limit > 0 and done >= args.limit:
                break

            batch_links: list[tuple[str, str]] = []  # (clean_id, cafe_id)

            for row in rows:
                if args.limit > 0 and done >= args.limit:
                    break
                cafe = {
                    "id": row[0], "name": row[1], "lat": row[2],
                    "lon": row[3], "provider": row[4],
                    "address": row[5] or "", "url": row[6] or "",
                    "metadata": row[7] or ""
                }
                try:
                    clean_id, was_created = process_cafe(conn, dbc, cafe, chain_names, args.embed)
                    batch_links.append((clean_id, cafe["id"]))
                    if was_created:
                        created += 1
                    else:
                        merged += 1
                except Exception as e:
                    errors += 1
                    if errors <= 5:
                        print(f"\n  ERROR on {cafe['id']}: {e}")
                done += 1
                now = time.time()
                if now - last_print >= 2.0:
                    print(f"\r{progress_bar(done, total, now - start)}"
                          f"  new={created} merged={merged} err={errors}",
                          end="", flush=True)
                    last_print = now

            # Batch update belongs_to_cafe_id — 1 dbc call per batch instead of per cafe
            if batch_links:
                dbc.executemany(
                    "UPDATE cafes SET belongs_to_cafe_id = ? WHERE id = ? AND belongs_to_cafe_id IS NULL",
                    batch_links
                )
                dbc.executemany(
                    "UPDATE images SET belongs_to_cafe_id = ? WHERE cafe_id = ?",
                    batch_links
                )

    except KeyboardInterrupt:
        print("\nPaused.")

    elapsed = time.time() - start
    print(f"\n\nDone in {elapsed:.1f}s ({elapsed/60:.1f}m)")
    print(f"  Processed:    {done}")
    print(f"  New:          {created}")
    print(f"  Merged:       {merged}")
    print(f"  Errors:       {errors}")

    # Final DB counts via dbc (authoritative)
    print(f"  clean_cafes:  {dbc.fetchval('SELECT COUNT(*) FROM clean_cafes')}")
    print(f"  chains:       {dbc.fetchval('SELECT COUNT(*) FROM cafe_chains')}")
    print(f"  cafes done:   {dbc.fetchval('SELECT COUNT(*) FROM cafes WHERE belongs_to_cafe_id IS NOT NULL')}")

    conn.close()


if __name__ == "__main__":
    main()
