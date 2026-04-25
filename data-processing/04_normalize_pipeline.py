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

MERGE_RADIUS_HARD = 9.0    # unconditional (GPS noise floor ~5-8m)
MERGE_RADIUS_SOFT = 20.0   # name-checked zone for typical OSM/Kakao GPS offset (8-13m)
MERGE_RADIUS_MAX = 150.0
NAME_SIM_THRESHOLD = 0.8   # standard threshold for 20-150m zone
SOFT_NAME_THRESHOLD = 0.44 # soft zone: just above unrelated Korean 5-char name sim (~0.4)


# ─── Read helpers (direct SQLite) ─────────────────────────────────────────────

def find_clean_cafes_nearby(conn, lat, lon, radius_m=50.0):
    min_lat, max_lat, min_lon, max_lon = lat_lon_bbox(lat, lon, radius_m)
    rows = conn.execute("""
        SELECT id, name, english_name, avg_lat, avg_lon, providers, source_ids, chain_id
        FROM clean_cafes
        WHERE avg_lat BETWEEN ? AND ? AND avg_lon BETWEEN ? AND ?
    """, (min_lat, max_lat, min_lon, max_lon)).fetchall()
    result = []
    for row in rows:
        dist = haversine_m(lat, lon, row[3], row[4])
        if dist <= radius_m:
            result.append({
                "id": row[0], "name": row[1], "english_name": row[2],
                "avg_lat": row[3], "avg_lon": row[4],
                "providers": row[5], "source_ids": row[6],
                "chain_id": row[7], "distance_m": dist,
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

            # Hard zone: unconditional (GPS noise floor, no two different cafes this close)
            if dist <= MERGE_RADIUS_HARD:
                matched_id = nc["id"]
                break

            # Compute best name similarity across raw / strip_branch / english
            sim = name_similarity(cafe["name"], nc["name"])
            sim2 = name_similarity(strip_branch(cafe["name"]), strip_branch(nc["name"]))
            if sim2["combined"] > sim["combined"]:
                sim = sim2
            cafe_en = (eng_lookup or {}).get(cafe["name"])
            nc_en = nc.get("english_name")
            if cafe_en and nc_en:
                sim_en = name_similarity(cafe_en, nc_en)
                if sim_en["combined"] > sim["combined"]:
                    sim = sim_en
            best_score = sim["combined"]

            # Soft zone (9-20m): accept lower threshold — GPS offset true matches
            # fall through on failure so a better-named candidate can still match
            if dist <= MERGE_RADIUS_SOFT and best_score >= SOFT_NAME_THRESHOLD:
                matched_id = nc["id"]
                break

            # Standard zone (20-150m): require high name similarity
            if dist > MERGE_RADIUS_SOFT and best_score >= NAME_SIM_THRESHOLD:
                matched_id = nc["id"]
                break

            # Chain canonical match — handles cross-language (e.g. "Compose Coffee" vs "컴포즈커피")
            # Works at any distance; critical for chains with Korean/English name mismatch
            if nc["chain_id"]:
                cr = is_chain_cafe(cafe["name"], chain_names)
                if cr["is_chain"]:
                    chain_base = cr.get("chain_name") or strip_branch(cafe["name"])
                    row = conn.execute(
                        "SELECT name, name_english FROM cafe_chains WHERE id = ?", (nc["chain_id"],)
                    ).fetchone()
                    if row:
                        nc_cr = is_chain_cafe(row[0], chain_names)
                        nc_canonical = nc_cr.get("chain_name") or row[1] or row[0]
                        if nc_canonical and nc_canonical == chain_base:
                            matched_id = nc["id"]
                            break
                        if name_similarity(chain_base, row[0])["combined"] >= 0.8:
                            matched_id = nc["id"]
                            break

            # Candidate for LLM fallback
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

    # ── Exclusive run guard ────────────────────────────────────────────────────
    lock_path = args.db + ".pipeline.lock"
    if os.path.exists(lock_path):
        try:
            with open(lock_path) as lf:
                info = lf.read().strip()
        except Exception:
            info = "(unreadable)"
        print(f"\nERROR: {args.db} is already locked by another pipeline run.")
        print(f"       Lock: {lock_path}")
        print(f"       Info: {info}")
        print(f"\n       If the previous run crashed, remove the lock manually:")
        print(f"       rm {lock_path}\n")
        sys.exit(1)

    import atexit
    with open(lock_path, "w") as lf:
        lf.write(f"pid={os.getpid()} db={args.db}")
    atexit.register(lambda: os.path.exists(lock_path) and os.remove(lock_path))

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

    def fetch_rows(provider_filter: str | None, exclude: bool = False) -> list:
        """Fetch unprocessed scraped_cafes rows, optionally filtered by provider."""
        q = ("SELECT id, name, lat, lon, provider, address, url, metadata FROM scraped_cafes "
             "WHERE belongs_to_cafe_id IS NULL")
        params: list = []
        if args.provider:
            q += " AND provider = ?"
            params.append(args.provider)
        if provider_filter:
            q += f" AND provider {'!=' if exclude else '='} ?"
            params.append(provider_filter)
        q += " ORDER BY id"
        _cur = conn.execute(q, params)
        rows = _cur.fetchall()
        _cur.close()
        conn.commit()
        return rows

    def run_pass(label: str, rows: list, all_created: list, all_merged: list) -> tuple[int, int, int]:
        """Process a batch of rows, return (done, created, merged, errors)."""
        if args.limit > 0:
            rows = rows[:args.limit]
        total = len(rows)
        if total == 0:
            print(f"  {label}: nothing to process.")
            return 0, 0, 0

        print(f"\n  {label}: {total} cafes")
        print("  Running... (Ctrl+C to pause, safe to restart)")

        done = created = merged = errors = 0
        provider_stats: dict[str, dict] = {}
        start = time.time()
        last_print = 0

        try:
            for batch_start in range(0, len(rows), 500):
                batch = rows[batch_start:batch_start + 500]
                batch_links: list[tuple[str, str]] = []

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
                    try:
                        clean_id, was_created = process_cafe(conn, dbc, cafe, chain_names, args.embed, eng_lookup)
                        batch_links.append((clean_id, cafe["id"]))
                        if was_created:
                            created += 1
                            provider_stats[prov]["created"] += 1
                            all_created.append(clean_id)
                        else:
                            merged += 1
                            provider_stats[prov]["merged"] += 1
                            all_merged.append(clean_id)
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
                                  f"  [{fmt_name(cafe['name'])}]")
                        print(f"\r  {progress_bar(done, total, now - start)}{suffix}",
                              end="", flush=True)
                        last_print = now

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
            print("\n  Paused.")

        elapsed = time.time() - start
        print(f"\n  {label} done in {elapsed:.1f}s — new={created} merged={merged} errors={errors}")
        if provider_stats:
            for prov, ps in provider_stats.items():
                pct = 100 * ps["merged"] / ps["done"] if ps["done"] > 0 else 0
                print(f"    {prov:<8} done={ps['done']:>6}  new={ps['created']:>6}  merged={ps['merged']:>5}  ({pct:.0f}% merged)")
        return done, created, merged

    all_created: list = []
    all_merged: list = []

    print("\nLoading scraped_cafes into memory...")

    # ── Pass 1: Kakao (insert-only, no spatial lookup — fast) ─────────────────
    print("\n━━━ Pass 1/2  Kakao (insert-only) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    kakao_rows = fetch_rows("kakao")
    d1, c1, m1 = run_pass("Kakao", kakao_rows, all_created, all_merged)

    # ── Pass 2: Non-kakao (spatial merge — slower, accurate ETA) ─────────────
    print("\n━━━ Pass 2/2  Non-kakao (spatial merge) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    other_rows = fetch_rows("kakao", exclude=True)
    d2, c2, m2 = run_pass("Non-kakao", other_rows, all_created, all_merged)

    done    = d1 + d2
    created = c1 + c2
    merged  = m1 + m2

    elapsed_total = 0  # individual passes track their own elapsed
    print(f"\n\nTotal: processed={done}  new={created}  merged={merged}")

    # Final DB counts via dbc (authoritative)
    print(f"\n  clean_cafes:  {dbc.fetchval('SELECT COUNT(*) FROM clean_cafes')}")
    print(f"  chains:       {dbc.fetchval('SELECT COUNT(*) FROM cafe_chains')}")
    print(f"  scraped done: {dbc.fetchval('SELECT COUNT(*) FROM scraped_cafes WHERE belongs_to_cafe_id IS NOT NULL')}")

    conn.close()


if __name__ == "__main__":
    main()
