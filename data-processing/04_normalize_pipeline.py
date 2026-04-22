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
Safe to restart: only processes scraped_cafes with belongs_to_cafe_id IS NULL.
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
sys.path.insert(0, os.path.join(_HERE, '..', 'scraper', 'lib'))

from cafe_norm_utils import (
    haversine_m, name_similarity, get_embedding, embed_to_blob,
    is_chain_cafe, lat_lon_bbox, get_english_name, strip_branch,
)
from db_client import DBClient

DB_PATH = os.path.abspath(os.path.join(_HERE, '..', 'data', 'seoul', 'clean.db'))

MERGE_RADIUS_AUTO = 8.0
MERGE_RADIUS_MAX = 150.0
NAME_SIM_THRESHOLD = 0.8


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

    # Match by name OR name_english (handles English canonical matching Korean DB entry)
    row = conn.execute(
        "SELECT id, name FROM cafe_chains WHERE name = ? OR name_english = ?",
        (chain_name, chain_name)
    ).fetchone()
    if row:
        _chain_id_cache[chain_name] = row[0]
        _chain_id_cache[row[1]] = row[0]
        return row[0]

    # Approximate match (both name and name_english)
    existing = conn.execute("SELECT id, name, name_english FROM cafe_chains").fetchall()
    for cid, cname, cen in existing:
        if name_similarity(chain_name, cname)["combined"] > 0.85:
            _chain_id_cache[chain_name] = cid
            return cid
        if cen and name_similarity(chain_name, cen)["combined"] > 0.85:
            _chain_id_cache[chain_name] = cid
            return cid

    chain_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"wc:chain:v1:{chain_name}"))
    dbc.execute("INSERT INTO cafe_chains (id, name) VALUES (?, ?)", (chain_id, chain_name))
    _chain_id_cache[chain_name] = chain_id
    return chain_id


def load_chain_cache(conn):
    rows = conn.execute("SELECT id, name, name_english FROM cafe_chains").fetchall()
    for cid, cname, cen in rows:
        _chain_id_cache[cname] = cid
        if cen:
            _chain_id_cache[cen] = cid
    return list(set(_chain_id_cache.keys()))


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

def create_clean_cafe(dbc: DBClient, cafe: dict, chain_id=None, emb_blob=None, eng_lookup: dict = None) -> str:
    # Deterministic: same scraped_cafe always produces same clean_cafe ID across runs
    clean_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"wc:cafe:v1:{cafe['id']}"))

    metadata = {}
    if cafe.get("metadata"):
        try:
            metadata[cafe["provider"]] = json.loads(cafe["metadata"])
        except:
            pass

    best_addr = extract_best_address(cafe)
    english_name = (eng_lookup or {}).get(cafe["name"], "")

    dbc.execute("""
        INSERT INTO clean_cafes
            (id, chain_id, name, english_name, avg_lat, avg_lon,
             address, url, providers, source_ids, name_embedding, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        clean_id, chain_id, cafe["name"], english_name,
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


def ask_llm_to_merge(cafe_name: str, candidates: list) -> Optional[str]:
    if not candidates:
        return None
    
    prompt = f"We are merging cafe data. We have a new scraped cafe named '{cafe_name}'.\n"
    prompt += "Here are the nearby existing cafes:\n"
    for i, c in enumerate(candidates):
        prompt += f"{i+1}. {c['name']} (Distance: {c['distance_m']:.1f}m)\n"
    prompt += "Does the new cafe belong to one of these? Reply ONLY with the number (e.g., '1') if it's the same cafe, or '0' if it is a completely new cafe. Do not provide any other text."
    
    from cafe_norm_utils import llm_generate
    import re
    resp = llm_generate(prompt, max_tokens=10)
    m = re.search(r'\d+', resp)
    if m:
        idx = int(m.group())
        if 1 <= idx <= len(candidates):
            return candidates[idx-1]["id"]
    return None

# ─── Core: process one cafe ────────────────────────────────────────────────────

def process_cafe(conn, dbc: DBClient, cafe: dict, chain_names: list,
                  embed_enabled: bool, eng_lookup: dict = None) -> tuple[str, bool]:
    """
    Returns (clean_cafe_id, was_created).
    was_created=True if new clean_cafe row created, False if merged into existing.
    Note: caller is responsible for updating scraped_cafes.belongs_to_cafe_id (batched).
    """
    lat, lon = cafe["lat"], cafe["lon"]

    emb_blob = None
    if embed_enabled:
        from cafe_norm_utils import get_embedding, embed_to_blob
        emb_vec = get_embedding(cafe["name"])
        if emb_vec is not None:
            emb_blob = embed_to_blob(emb_vec)

    conn.commit()
    
    matched_id = None
    if cafe["provider"] != "kakao":
        nearby = find_clean_cafes_nearby(conn, lat, lon, MERGE_RADIUS_MAX)
        llm_candidates = []

        for nc in nearby:
            providers = json.loads(nc["providers"] or "[]")
            if cafe["provider"] in providers:
                continue

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
                    chain_base = cr.get("chain_name") or strip_branch(cafe["name"])
                    row = conn.execute(
                        "SELECT name FROM cafe_chains WHERE id = ?", (nc["chain_id"],)
                    ).fetchone()
                    if row and name_similarity(chain_base, row[0])["combined"] >= 0.8:
                        matched_id = nc["id"]
                        break
            
            # If we didn't break, it's a candidate for LLM
            llm_candidates.append(nc)
            
        if not matched_id and llm_candidates:
            # Sort candidates by distance and take top 5
            llm_candidates.sort(key=lambda x: x["distance_m"])
            matched_id = ask_llm_to_merge(cafe["name"], llm_candidates[:5])

    if matched_id:
        merge_into_clean_cafe(conn, dbc, matched_id, cafe)
        # belongs_to_cafe_id update deferred to batch (see main loop)
        return matched_id, False

    # Create new
    chain_id = None
    cr = is_chain_cafe(cafe["name"], chain_names)
    if cr["is_chain"]:
        base = cr.get("chain_name") or strip_branch(cafe["name"])
        chain_id = get_or_create_chain(conn, dbc, base)
        if base not in chain_names:
            chain_names.append(base)

    clean_id = create_clean_cafe(dbc, cafe, chain_id, emb_blob, eng_lookup)
    # belongs_to_cafe_id update deferred to batch (see main loop)
    return clean_id, True


# ─── Progress ─────────────────────────────────────────────────────────────────

def progress_bar(done, total, elapsed, bar_len=28):
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


def fmt_name(name: str, maxlen: int = 20) -> str:
    return name if len(name) <= maxlen else name[:maxlen - 1] + "…"


# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--embed", action="store_true", help="Generate embeddings (slow)")
    parser.add_argument("--provider", help="Only process this provider")
    parser.add_argument("--reset", action="store_true", help="Reset clean data completely before running")
    parser.add_argument("--db", help="Override SQLite DB path (clean.db)", default=DB_PATH)
    parser.add_argument("--socket", help="Override db_server socket path", default="/tmp/workcafe_play_db.sock")
    parser.add_argument("--englishify-db", default=os.path.abspath(os.path.join(_HERE, '..', 'data', 'seoul', 'englishify.db')),
                        help="Path to englishify.db translation cache")
    parser.add_argument("--no-backup", action="store_true", help="Skip automatic backup of clean.db before run")
    args = parser.parse_args()

    if not args.reset and not args.no_backup:
        backup_script = os.path.abspath(os.path.join(_HERE, '..', 'scripts', 'backup-clean.sh'))
        if os.path.exists(backup_script):
            import subprocess
            print("Running backup-clean before pipeline...")
            result = subprocess.run([backup_script, "--db", args.db], capture_output=False)
            if result.returncode != 0:
                print("Warning: backup failed (continuing anyway)")
        else:
            print(f"Warning: backup script not found at {backup_script}")

    conn = sqlite3.connect(args.db, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA cache_size=10000")
    dbc = DBClient(socket_path=args.socket)

    # Load English name lookup from englishify.db
    eng_lookup: dict = {}
    if os.path.exists(args.englishify_db):
        eng_conn = sqlite3.connect(args.englishify_db, timeout=10)
        rows = eng_conn.execute("SELECT korean_name, english_name FROM name_translations WHERE english_name IS NOT NULL").fetchall()
        eng_lookup = {kr: en for kr, en in rows}
        eng_conn.close()
        print(f"English lookup loaded: {len(eng_lookup)} names from englishify.db")
    else:
        print(f"Warning: englishify.db not found at {args.englishify_db} — english_name will be empty")

    if args.reset:
        print("WARNING: Resetting all clean data...")
        conn.execute("DELETE FROM clean_cafes")
        conn.execute("DELETE FROM cafe_chains")
        conn.execute("UPDATE scraped_cafes SET belongs_to_cafe_id = NULL")
        conn.execute("UPDATE images SET belongs_to_cafe_id = NULL")
        conn.commit()
        print("Clean data reset.")

    chain_names = load_chain_cache(conn)
    print(f"Chains loaded: {len(chain_names)}")

    fetch_q = ("SELECT id, name, lat, lon, provider, address, url, metadata FROM scraped_cafes "
               "WHERE belongs_to_cafe_id IS NULL")
    params = []
    if args.provider:
        fetch_q += " AND provider = ?"
        params.append(args.provider)
    fetch_q += " ORDER BY CASE provider WHEN 'kakao' THEN 1 WHEN 'naver' THEN 2 WHEN 'google' THEN 3 WHEN 'osm' THEN 4 ELSE 5 END, id"

    # Pre-fetch all rows into memory so the cursor is closed before processing.
    # Keeping an open cursor holds a SQLite read-transaction on conn, which prevents
    # conn.commit() inside process_cafe from refreshing the clean_cafes snapshot —
    # causing every cafe to create a new entry instead of merging into existing ones.
    print("Loading scraped_cafes into memory...")
    _cur = conn.execute(fetch_q, params)
    all_rows = _cur.fetchall()
    _cur.close()   # close cursor before commit so the read-transaction is fully released
    del _cur
    if args.limit > 0:
        all_rows = all_rows[:args.limit]
    total = len(all_rows)
    conn.commit()  # now properly refreshes snapshot; process_cafe's conn.commit() will too

    print(f"Cafes to process: {total}")
    print("Running... (Ctrl+C to pause, safe to restart)")

    done = created = merged = errors = 0
    provider_stats: dict[str, dict] = {}   # provider -> {done, created, merged}
    start = time.time()
    last_print = 0
    current_provider = ""

    try:
        for batch_start in range(0, len(all_rows), 500):
            batch = all_rows[batch_start:batch_start + 500]
            batch_links: list[tuple[str, str]] = []  # (clean_id, cafe_id)

            for row in batch:
                cafe = {
                    "id": row[0], "name": row[1], "lat": row[2],
                    "lon": row[3], "provider": row[4],
                    "address": row[5] or "", "url": row[6] or "",
                    "metadata": row[7] or ""
                }
                prov = cafe["provider"]
                if prov not in provider_stats:
                    provider_stats[prov] = {"done": 0, "created": 0, "merged": 0}
                if prov != current_provider:
                    if current_provider:
                        ps = provider_stats[current_provider]
                        print(f"\n  [{current_provider}] done: {ps['done']}  new={ps['created']} merged={ps['merged']}")
                    current_provider = prov
                    print(f"\n  → Processing provider: {prov}")
                try:
                    clean_id, was_created = process_cafe(conn, dbc, cafe, chain_names, args.embed, eng_lookup)
                    batch_links.append((clean_id, cafe["id"]))
                    if was_created:
                        created += 1
                        provider_stats[prov]["created"] += 1
                    else:
                        merged += 1
                        provider_stats[prov]["merged"] += 1
                except Exception as e:
                    errors += 1
                    if errors <= 5:
                        print(f"\n  ERROR on {cafe['id']} ({prov} / {cafe['name'][:30]}): {e}")
                done += 1
                provider_stats[prov]["done"] += 1
                now = time.time()
                if now - last_print >= 2.0:
                    ps = provider_stats[prov]
                    suffix = (f"  {prov} new={ps['created']} merged={ps['merged']}"
                              f"  total new={created} merged={merged} err={errors}"
                              f"  [{fmt_name(cafe['name'])}]")
                    print(f"\r{progress_bar(done, total, now - start)}{suffix}",
                          end="", flush=True)
                    last_print = now

            # Batch update belongs_to_cafe_id — 1 dbc call per batch instead of per cafe
            if batch_links:
                dbc.executemany(
                    "UPDATE scraped_cafes SET belongs_to_cafe_id = ? WHERE id = ? AND belongs_to_cafe_id IS NULL",
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
    if provider_stats:
        print(f"\n  Per-provider:")
        for prov, ps in provider_stats.items():
            pct = 100 * ps["merged"] / ps["done"] if ps["done"] > 0 else 0
            print(f"    {prov:<8} done={ps['done']:>6}  new={ps['created']:>6}  merged={ps['merged']:>5}  ({pct:.0f}% merged)")

    # Final DB counts via dbc (authoritative)
    print(f"\n  clean_cafes:  {dbc.fetchval('SELECT COUNT(*) FROM clean_cafes')}")
    print(f"  chains:       {dbc.fetchval('SELECT COUNT(*) FROM cafe_chains')}")
    print(f"  scraped done: {dbc.fetchval('SELECT COUNT(*) FROM scraped_cafes WHERE belongs_to_cafe_id IS NOT NULL')}")

    conn.close()


if __name__ == "__main__":
    main()
