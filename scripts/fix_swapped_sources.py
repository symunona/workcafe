#!/usr/bin/env python3
"""
fix_swapped_sources.py — one-time repair for the <9m hard-zone merge swap.

Background: before the name-aware hard-zone veto (04_normalize_pipeline.different_business),
the unconditional distance merge attached source records to whichever co-located
clean_cafe was nearest, ignoring names. Two different businesses stacked in one
building (e.g. Hollys Coffee + a brunch cafe) ended up with each other's source
records swapped. The pipeline fix prevents NEW swaps; this script repairs the rows
already committed.

Method — extract & re-place (handles the symmetric swap without provider clashes):
  1. Flag every source whose brand identity clearly differs from its cluster
     (same different_business() the live pipeline now uses to veto the merge).
  2. Pull all flagged sources out of their clusters.
  3. Re-place each into the best-matching nearby clean_cafe (brand/name similarity,
     no provider collision, not a different business). No good target → restored
     to its original cluster (never dropped).
  4. Recompute every touched cluster: providers, source_ids, avg coords, display
     name (provider priority), chain_id, english_name. Empty clusters deleted.
  5. Repoint scraped_cafes.belongs_to_cafe_id and images.belongs_to_cafe_id.

Dry-run by default. --apply writes. Scope with --bbox / --center / --scan-all.
ALWAYS back up clean.db before --apply (script refuses unless --no-backup-ok).
"""
import os
import sys
import json
import argparse
import sqlite3
import importlib
import shutil
import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_DP = os.path.join(_HERE, '..', 'data-processing')
sys.path.insert(0, os.path.join(_HERE, '..', 'scraper', 'lib'))
sys.path.insert(0, _DP)

from cafe_norm_utils import (
    is_chain_cafe, strip_branch, name_similarity, haversine_m, lat_lon_bbox,
)
norm = importlib.import_module("04_normalize_pipeline")
different_business = norm.different_business
find_clean_cafes_nearby = norm.find_clean_cafes_nearby

DB_PATH = os.path.abspath(os.path.join(_HERE, '..', 'data', 'seoul', 'clean.db'))
ENGLISHIFY_DB = os.path.abspath(os.path.join(_HERE, '..', 'data', 'seoul', 'englishify.db'))

MOVE_RADIUS_M = 30.0      # a swapped sibling is in the same building → metres apart
PLACE_MIN_SCORE = 0.30    # below this, no confident target → keep original
PROVIDER_PRIORITY = ["kakao", "naver", "google", "osm", "apple"]


# ─── helpers ──────────────────────────────────────────────────────────────────

def load_chain_index(conn):
    """name / name_english → chain_id, and id → (name, name_english)."""
    by_name, by_id = {}, {}
    for cid, name, en in conn.execute("SELECT id, name, name_english FROM cafe_chains"):
        by_id[cid] = (name, en)
        if name:
            by_name[name] = cid
        if en:
            by_name[en] = cid
    return by_name, by_id


def english_lookup(name, eng_conn):
    if not eng_conn or not name:
        return None
    row = eng_conn.execute(
        "SELECT english_name FROM name_translations WHERE korean_name = ?", (name,)
    ).fetchone()
    return row[0] if row and row[0] else None


def englishify_entry(name, eng_conn):
    """(english_name, model) for a Korean name, or (None, None)."""
    if not eng_conn or not name:
        return None, None
    row = eng_conn.execute(
        "SELECT english_name, model FROM name_translations WHERE korean_name = ?", (name,)
    ).fetchone()
    return (row[0], row[1]) if row else (None, None)


def pick_display_name(source_rows):
    """source_rows: list of dicts with provider, name. Highest-priority provider's name."""
    def rank(p):
        return PROVIDER_PRIORITY.index(p) if p in PROVIDER_PRIORITY else len(PROVIDER_PRIORITY)
    best = min(source_rows, key=lambda s: rank(s["provider"]))
    return best["name"]


def resolve_chain(name, chain_by_name, by_id, eng_conn):
    """Return (chain_id, english_name) for a (possibly branch-suffixed) name."""
    cr = is_chain_cafe(name, [])  # known-brand list only (fast, high precision)
    if cr["is_chain"]:
        canonical = cr["chain_name"]  # canonical English
        cid = chain_by_name.get(canonical)
        if cid:
            return cid, (by_id[cid][1] or canonical)
        return None, canonical
    # not a recognized chain → english from the translation cache
    return None, english_lookup(name, eng_conn)


_GENERIC_TOKENS = {
    "커피", "카페", "coffee", "cafe", "점", "branch", "로스터리", "로스터스",
    "roastery", "roasters", "the", "&", "bakery", "베이커리",
}


def _tokens(s):
    return {t for t in strip_branch(s).lower().split() if t and t not in _GENERIC_TOKENS}


def containment(a, b):
    """Fraction of the smaller name's (non-generic) tokens shared with the other.

    Robust where Levenshtein fails: Google's verbose '워킹홀리데이 해운대 workingholiday
    brunch cafe bakery' is length-penalized vs the short kakao '워킹홀리데이 해운대', but
    its tokens fully contain it → 1.0. Generic words (커피/cafe/점…) excluded so two
    unrelated '… 커피' shops don't score as related on the shared suffix alone.
    """
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def place_score(src_name, src_en, nc, tgt_source_names):
    """Best similarity of a source against a candidate cluster.

    Compares against the cluster's display name AND each of its remaining source
    names (the swapped sibling's true names), via Levenshtein, English-name match,
    and token containment. Source-name comparison is what makes a swap re-placement
    reliable — the moved record matches its real siblings, not the stale display name.
    """
    cands = [nc["name"]] + list(tgt_source_names)
    sim = 0.0
    for cand in cands:
        sim = max(sim, name_similarity(src_name, cand)["combined"])
        sim = max(sim, name_similarity(strip_branch(src_name), strip_branch(cand))["combined"])
        sim = max(sim, containment(src_name, cand))
    if src_en and nc.get("english_name"):
        sim = max(sim, name_similarity(src_en, nc["english_name"])["combined"])
    return sim


# ─── core ─────────────────────────────────────────────────────────────────────

def run(conn, eng_conn, where_sql, where_args, apply, verbose):
    chain_by_name, chain_by_id = load_chain_index(conn)

    # Clusters in scope.
    clusters = {}
    for cid, name, en, lat, lon, providers, source_ids, chain_id in conn.execute(
        f"""SELECT id, name, english_name, avg_lat, avg_lon, providers, source_ids, chain_id
              FROM clean_cafes WHERE {where_sql}""", where_args):
        clusters[cid] = {
            "id": cid, "name": name, "english_name": en, "chain_id": chain_id,
            "lat": lat, "lon": lon,
            "sources": json.loads(source_ids or "[]"),
        }
    if not clusters:
        print("  no clusters in scope.")
        return [], []

    # Source rows for everything referenced.
    all_sids = [s for c in clusters.values() for s in c["sources"]]
    scraped = {}
    CH = 400
    for i in range(0, len(all_sids), CH):
        chunk = all_sids[i:i + CH]
        q = ",".join("?" * len(chunk))
        for sid, prov, nm, slat, slon in conn.execute(
            f"SELECT id, provider, name, lat, lon FROM scraped_cafes WHERE id IN ({q})", chunk):
            scraped[sid] = {"id": sid, "provider": prov, "name": nm or "", "lat": slat, "lon": slon}

    # 1. flag misplaced sources.
    misplaced = []  # (sid, orig_cluster_id)
    for c in clusters.values():
        nc = {"name": c["name"], "chain_id": c["chain_id"]}
        for sid in c["sources"]:
            S = scraped.get(sid)
            if not S:
                continue
            if different_business(conn, {"name": S["name"]}, nc, []):
                misplaced.append((sid, c["id"]))

    if not misplaced:
        print("  no misplaced sources detected in scope.")
        return [], []

    # 2. extract.
    for sid, cid in misplaced:
        if sid in clusters[cid]["sources"]:
            clusters[cid]["sources"].remove(sid)

    # 3. re-place each flagged source into the best nearby cluster.
    moves = []  # (sid, orig_cid, new_cid, score)
    for sid, orig_cid in misplaced:
        S = scraped[sid]
        src_en = english_lookup(S["name"], eng_conn)
        cands = find_clean_cafes_nearby(conn, S["lat"], S["lon"], MOVE_RADIUS_M)
        best, best_score = None, -1.0
        for nc in cands:
            tgt = clusters.get(nc["id"])
            if tgt is None:
                continue
            # provider collision against the POST-extraction occupancy
            tgt_provs = {scraped[x]["provider"] for x in tgt["sources"] if x in scraped}
            if S["provider"] in tgt_provs:
                continue
            if different_business(conn, {"name": S["name"]},
                                  {"name": tgt["name"], "chain_id": tgt["chain_id"]}, []):
                continue
            tgt_names = [scraped[x]["name"] for x in tgt["sources"] if x in scraped]
            sc = place_score(S["name"], src_en, nc, tgt_names)
            if sc > best_score:
                best, best_score = nc["id"], sc
        new_cid = best if (best and best_score >= PLACE_MIN_SCORE) else orig_cid
        clusters[new_cid]["sources"].append(sid)
        moves.append((sid, orig_cid, new_cid, best_score))

    # 4. recompute clusters whose membership ACTUALLY changed (real moves only —
    #    kept-in-place sources were restored to their original cluster, so those
    #    clusters are byte-identical and must not be rewritten).
    real_moves = [m for m in moves if m[1] != m[2]]
    touched = {cid for (_s, o, n, _sc) in real_moves for cid in (o, n)}
    plan = {"updates": [], "deletes": [], "moves": moves}
    poisoned_names = set()  # englishify entries left stale by a departed Google record
    for cid in touched:
        c = clusters[cid]
        srcs = [scraped[s] for s in c["sources"] if s in scraped]
        if not srcs:
            plan["deletes"].append(cid)
            continue
        name = pick_display_name(srcs)
        chain_id, english = resolve_chain(name, chain_by_name, chain_by_id, eng_conn)
        if not chain_id:
            # The englishify cache may hold a google_native English adopted from a
            # Google record that we just moved AWAY (the original swap poisoned it:
            # '워킹홀리데이 해운대' → 'Hollys Coffee'). If the adopted string no longer
            # matches any Google source still in this cluster, it's stale → blank it
            # and flag the cache entry for re-translation by the next englishify run.
            e_en, e_model = englishify_entry(name, eng_conn)
            cur_google = {s["name"] for s in srcs if s["provider"] == "google"}
            if e_model == "google_native" and e_en and e_en not in cur_google:
                english = None
                poisoned_names.add(name)
            elif not english:
                english = c["english_name"]
        providers = sorted({s["provider"] for s in srcs})
        avg_lat = sum(s["lat"] for s in srcs) / len(srcs)
        avg_lon = sum(s["lon"] for s in srcs) / len(srcs)
        plan["updates"].append({
            "id": cid, "name": name, "english_name": english, "chain_id": chain_id,
            "providers": json.dumps(providers),
            "source_ids": json.dumps([s["id"] for s in srcs]),
            "avg_lat": avg_lat, "avg_lon": avg_lon,
        })
    plan["poisoned_names"] = poisoned_names

    # ── report ──
    print(f"  clusters in scope : {len(clusters)}")
    print(f"  flagged sources   : {len(misplaced)}")
    print(f"  reassignments     : {len(real_moves)}  (kept-in-place: {len(moves) - len(real_moves)})")
    print(f"  clusters updated  : {len(plan['updates'])}   deleted: {len(plan['deletes'])}")
    print(f"  englishify resets : {len(plan['poisoned_names'])}  (stale google_native → retranslate)")
    if verbose:
        for sid, o, n, sc in real_moves:
            print(f"    move {sid:42}  {o[:8]} → {n[:8]}  (score {sc:.2f}, '{scraped[sid]['name'][:30]}')")

    if not apply:
        print("  [dry-run] no writes. pass --apply to commit.")
        return plan["updates"], real_moves

    # 5. write.
    cur = conn.cursor()
    for u in plan["updates"]:
        cur.execute("""
            UPDATE clean_cafes
               SET name=?, english_name=?, chain_id=?, providers=?, source_ids=?,
                   avg_lat=?, avg_lon=?, updated_at=CURRENT_TIMESTAMP
             WHERE id=?
        """, (u["name"], u["english_name"], u["chain_id"], u["providers"],
              u["source_ids"], u["avg_lat"], u["avg_lon"], u["id"]))
    for cid in plan["deletes"]:
        cur.execute("DELETE FROM clean_cafes WHERE id=?", (cid,))
    for sid, _o, n, _sc in real_moves:
        cur.execute("UPDATE scraped_cafes SET belongs_to_cafe_id=? WHERE id=?", (n, sid))
        cur.execute("UPDATE images SET belongs_to_cafe_id=? WHERE cafe_id=?", (n, sid))
    conn.commit()

    # Reset poisoned englishify entries (separate DB) so the next englishify run
    # re-translates the Korean name instead of carrying the departed Google name.
    if plan["poisoned_names"] and os.path.exists(ENGLISHIFY_DB):
        ew = sqlite3.connect(ENGLISHIFY_DB, timeout=30)
        ew.executemany(
            "UPDATE name_translations SET english_name=NULL, model=NULL, translated_at=NULL "
            "WHERE korean_name=? AND model='google_native'",
            [(n,) for n in plan["poisoned_names"]],
        )
        ew.commit()
        ew.close()

    print(f"  [applied] {len(plan['updates'])} updated, {len(plan['deletes'])} deleted, "
          f"{len(real_moves)} sources repointed, {len(plan['poisoned_names'])} englishify resets.")
    return plan["updates"], real_moves


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--bbox", nargs=4, type=float, metavar=("MIN_LAT", "MAX_LAT", "MIN_LON", "MAX_LON"))
    ap.add_argument("--center", nargs=3, type=float, metavar=("LAT", "LON", "RADIUS_KM"))
    ap.add_argument("--scan-all", action="store_true", help="whole DB (detection/dry-run)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--no-backup-ok", action="store_true", help="skip the pre-apply backup guard")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    if args.bbox:
        where = "avg_lat BETWEEN ? AND ? AND avg_lon BETWEEN ? AND ?"
        wargs = (args.bbox[0], args.bbox[1], args.bbox[2], args.bbox[3])
    elif args.center:
        lat, lon, rkm = args.center
        mnla, mxla, mnlo, mxlo = lat_lon_bbox(lat, lon, rkm * 1000)
        where = "avg_lat BETWEEN ? AND ? AND avg_lon BETWEEN ? AND ?"
        wargs = (mnla, mxla, mnlo, mxlo)
    elif args.scan_all:
        where, wargs = "1=1", ()
    else:
        ap.error("specify --bbox, --center, or --scan-all")

    if args.apply and not args.no_backup_ok:
        bdir = os.path.join(os.path.dirname(args.db), "backups",
                            "swapfix-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        os.makedirs(bdir, exist_ok=True)
        dst = os.path.join(bdir, os.path.basename(args.db))
        src = sqlite3.connect(args.db)
        src.execute("VACUUM INTO ?", (dst,))
        src.close()
        print(f"  backup → {dst}")

    conn = sqlite3.connect(args.db, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    eng_conn = sqlite3.connect(f"file:{ENGLISHIFY_DB}?mode=ro", uri=True) if os.path.exists(ENGLISHIFY_DB) else None
    print(f"DB: {args.db}   scope: {wargs if wargs else 'ALL'}   apply={args.apply}")
    run(conn, eng_conn, where, wargs, args.apply, args.verbose)
    conn.close()


if __name__ == "__main__":
    main()
