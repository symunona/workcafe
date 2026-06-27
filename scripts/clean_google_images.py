#!/usr/bin/env python3
"""
clean_google_images.py — remove junk Google Maps images that the old image
scraper (pre-fix) ingested by sweeping the whole place-page DOM.

Two classes of junk are handled:

  1. AVATARS  — reviewer profile pictures. Deterministic: their image_url is a
     googleusercontent.com path under /a/ or /a-/ . ~20% of all google rows.

  2. GPS-MISMATCH — a downloaded photo whose embedded EXIF GPS is more than
     --gps-meters from the cafe's coordinates is almost certainly a photo of a
     different place. NOTE: Google strips EXIF from images it serves, so on the
     current dataset this finds ~0 rows. Kept as a correctness/future-proof net
     (also reusable for kakao/naver, which DO carry EXIF).

The cross-brand "similar places" carousel thumbnails (Coffee Bean / Twosome /
Mammoth leaking into a Starbucks entry) are NOT detectable from already-
downloaded files — they share the same /gps-cs-s/ host as real photos and carry
no EXIF. Those are removed by re-scraping with the fixed scraper
(scraper_google_images_v1.py), not by this cleaner. See --list-polluted-cafes
to get the set of cafes that should be purged + re-scraped.

DATA SAFETY: dry-run by default. Prints exact scope. Pass --apply to delete.

DB targets:
  - scraped.db is the LIVE scraper DB (served by db_server socket). We mutate it
    through the socket so we don't fight db_server's writer lock.
  - clean.db is read by the API; mutated directly (WAL allows reader + 1 writer).

Usage:
  python scripts/clean_google_images.py --avatars                      # dry-run, both DBs
  python scripts/clean_google_images.py --avatars --apply              # delete avatars
  python scripts/clean_google_images.py --gps --gps-meters 30          # dry-run gps net
  python scripts/clean_google_images.py --avatars --gps --apply --db both
  python scripts/clean_google_images.py --list-polluted-cafes > cafes.txt
"""
import argparse
import math
import os
import sqlite3
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(_ROOT, "data", "seoul")
SCRAPED_DB = os.path.join(DATA_DIR, "scraped.db")
CLEAN_DB = os.path.join(DATA_DIR, "clean.db")
SOCKET_PATH = "/tmp/workcafe_db.sock"

AVATAR_PRED = (
    "(image_url LIKE '%googleusercontent.com/a/%' "
    "OR image_url LIKE '%googleusercontent.com/a-/%')"
)


# ── DB access ────────────────────────────────────────────────────────────────
# scraped.db → via db_server socket; clean.db → direct sqlite3.

def _socket_request(payload):
    import json
    import socket
    import struct
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(SOCKET_PATH)
    data = json.dumps(payload).encode()
    sock.sendall(struct.pack(">I", len(data)) + data)
    hdr = b""
    while len(hdr) < 4:
        hdr += sock.recv(4 - len(hdr))
    length = struct.unpack(">I", hdr)[0]
    buf = b""
    while len(buf) < length:
        buf += sock.recv(min(65536, length - len(buf)))
    sock.close()
    resp = json.loads(buf.decode())
    if not resp.get("ok", True) and "error" in resp:
        raise RuntimeError(resp["error"])
    return resp


class DB:
    """Uniform query/exec over either the scraped.db socket or a direct file."""

    def __init__(self, label, path, via_socket):
        self.label = label
        self.path = path
        self.via_socket = via_socket
        self._conn = None
        if not via_socket:
            self._conn = sqlite3.connect(path, timeout=60)

    def query(self, sql, params=()):
        if self.via_socket:
            return _socket_request({"op": "fetchall", "sql": sql, "params": list(params)}).get("rows", [])
        return self._conn.execute(sql, params).fetchall()

    def execute(self, sql, params=()):
        if self.via_socket:
            _socket_request({"op": "execute", "sql": sql, "params": list(params)})
        else:
            self._conn.execute(sql, params)
            self._conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()


def open_targets(which):
    targets = []
    if which in ("scraped", "both"):
        if os.path.exists(SOCKET_PATH):
            targets.append(DB("scraped.db", SCRAPED_DB, via_socket=True))
        else:
            print("WARN: db_server socket not found; opening scraped.db directly "
                  "(ensure no scraper is writing).", file=sys.stderr)
            targets.append(DB("scraped.db", SCRAPED_DB, via_socket=False))
    if which in ("clean", "both"):
        targets.append(DB("clean.db", CLEAN_DB, via_socket=False))
    return targets


# ── geo ──────────────────────────────────────────────────────────────────────

def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def read_exif_gps(abs_path):
    try:
        from PIL import Image
        from PIL.ExifTags import GPSTAGS, TAGS
    except ImportError:
        return None
    try:
        img = Image.open(abs_path)
        exif = img._getexif()
        if not exif:
            return None
        named = {TAGS.get(k, k): v for k, v in exif.items()}
        gps = named.get("GPSInfo")
        if not gps:
            return None
        g = {GPSTAGS.get(k, k): v for k, v in gps.items()}
        lat, lon = g.get("GPSLatitude"), g.get("GPSLongitude")
        if not lat or not lon:
            return None
        def dec(v):
            return float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600
        la = dec(lat) * (-1 if g.get("GPSLatitudeRef", "N") != "N" else 1)
        lo = dec(lon) * (-1 if g.get("GPSLongitudeRef", "E") != "E" else 1)
        return la, lo
    except Exception:
        return None


def abs_of(local_path):
    # local_path is like /images/google/<safe>/images/img_0.jpg → data/seoul/google/...
    rel = local_path.lstrip("/")
    if rel.startswith("images/"):
        rel = rel[len("images/"):]
    return os.path.join(DATA_DIR, rel)


# ── file deletion (only when no surviving row references the file) ─────────────

def maybe_unlink(local_path, surviving_paths, apply, removed_counter):
    if not local_path or local_path in surviving_paths:
        return
    ap = abs_of(local_path)
    if os.path.exists(ap):
        if apply:
            try:
                os.remove(ap)
            except OSError as e:
                print(f"  unlink failed {ap}: {e}", file=sys.stderr)
                return
        removed_counter[0] += 1


# ── phases ─────────────────────────────────────────────────────────────────────

def all_surviving_paths(db, deleting_ids):
    """local_paths still referenced by google rows NOT in deleting_ids."""
    rows = db.query("SELECT id, local_path FROM images WHERE provider='google'")
    dset = set(deleting_ids)
    return {lp for (rid, lp) in rows if rid not in dset and lp}


def phase_avatars(db, apply):
    rows = db.query(f"SELECT id, local_path FROM images WHERE provider='google' AND {AVATAR_PRED}")
    ids = [r[0] for r in rows]
    print(f"[{db.label}] avatars: {len(ids)} rows")
    if not ids:
        return 0, 0
    surviving = all_surviving_paths(db, ids)
    removed = [0]
    for _rid, lp in rows:
        maybe_unlink(lp, surviving, apply, removed)
    if apply:
        # delete in chunks
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            ph = ",".join("?" * len(chunk))
            db.execute(f"DELETE FROM images WHERE id IN ({ph})", chunk)
    print(f"[{db.label}] avatars: {'DELETED' if apply else 'would delete'} "
          f"{len(ids)} rows, {removed[0]} files")
    return len(ids), removed[0]


def phase_gps(db, meters, apply):
    rows = db.query("""
        SELECT i.id, i.local_path, c.avg_lat, c.avg_lon
        FROM images i JOIN clean_cafes c ON c.id = i.belongs_to_cafe_id
        WHERE i.provider='google' AND i.belongs_to_cafe_id IS NOT NULL
    """) if db.label == "clean.db" else db.query("""
        SELECT i.id, i.local_path, s.lat, s.lon
        FROM images i JOIN scraped_cafes s ON s.id = i.cafe_id
        WHERE i.provider='google'
    """)
    checked = 0
    far_ids = []
    far_rows = []
    for rid, lp, clat, clon in rows:
        if clat is None or clon is None or not lp:
            continue
        ap = abs_of(lp)
        if not os.path.exists(ap):
            continue
        gps = read_exif_gps(ap)
        checked += 1
        if gps is None:
            continue
        d = haversine_m(gps[0], gps[1], clat, clon)
        if d > meters:
            far_ids.append(rid)
            far_rows.append((rid, lp))
    print(f"[{db.label}] gps: checked {checked} files w/ readable EXIF, "
          f"{len(far_ids)} farther than {meters} m")
    if not far_ids:
        return 0, 0
    surviving = all_surviving_paths(db, far_ids)
    removed = [0]
    for _rid, lp in far_rows:
        maybe_unlink(lp, surviving, apply, removed)
    if apply:
        for i in range(0, len(far_ids), 500):
            chunk = far_ids[i:i + 500]
            ph = ",".join("?" * len(chunk))
            db.execute(f"DELETE FROM images WHERE id IN ({ph})", chunk)
    print(f"[{db.label}] gps: {'DELETED' if apply else 'would delete'} "
          f"{len(far_ids)} rows, {removed[0]} files")
    return len(far_ids), removed[0]


def phase_purge(db, cafe_ids, apply, delete_files):
    """Delete ALL google image rows for the given cafe_ids so the fixed scraper
    re-fetches them. Optionally delete the on-disk image dirs (required for a
    clean rescrape — the scraper skips files that already exist)."""
    cset = set(cafe_ids)
    rows = db.query("SELECT id, cafe_id, local_path FROM images WHERE provider='google'")
    target = [(rid, lp) for (rid, cid, lp) in rows if cid in cset]
    ids = [t[0] for t in target]
    dirs = set()
    for _rid, lp in target:
        if lp:
            dirs.add(os.path.dirname(abs_of(lp)))
    print(f"[{db.label}] purge: {len(ids)} google rows across {len(cset)} cafes; "
          f"{len(dirs)} image dirs")
    if apply and ids:
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            ph = ",".join("?" * len(chunk))
            db.execute(f"DELETE FROM images WHERE id IN ({ph})", chunk)
    removed_dirs = 0
    if delete_files:
        import shutil
        for d in dirs:
            if os.path.isdir(d):
                if apply:
                    try:
                        shutil.rmtree(d)
                    except OSError as e:
                        print(f"  rmtree failed {d}: {e}", file=sys.stderr)
                        continue
                removed_dirs += 1
    print(f"[{db.label}] purge: {'DELETED' if apply else 'would delete'} "
          f"{len(ids)} rows, {removed_dirs} dirs")
    return len(ids), removed_dirs


def list_polluted_cafes(db):
    """cafe_ids (scraped) that carry >=1 avatar → likely also carry carousel junk
    → should be purged and re-scraped with the fixed scraper."""
    rows = db.query(
        f"SELECT DISTINCT cafe_id FROM images WHERE provider='google' AND {AVATAR_PRED}")
    return [r[0] for r in rows]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", choices=["scraped", "clean", "both"], default="both")
    ap.add_argument("--avatars", action="store_true", help="remove reviewer-avatar rows")
    ap.add_argument("--gps", action="store_true", help="remove rows with EXIF GPS too far from cafe")
    ap.add_argument("--gps-meters", type=float, default=30.0)
    ap.add_argument("--list-polluted-cafes", action="store_true",
                    help="print scraped.db cafe_ids that have avatars (for purge+rescrape)")
    ap.add_argument("--purge-cafes", metavar="FILE",
                    help="delete ALL google rows for cafe_ids listed in FILE (one per line)")
    ap.add_argument("--delete-files", action="store_true",
                    help="with --purge-cafes: also rm the cafes' on-disk image dirs")
    ap.add_argument("--apply", action="store_true", help="actually delete (default: dry-run)")
    args = ap.parse_args()

    if args.list_polluted_cafes:
        db = open_targets("scraped")[0]
        for cid in list_polluted_cafes(db):
            print(cid)
        db.close()
        return

    if not (args.avatars or args.gps or args.purge_cafes):
        ap.error("nothing to do: pass --avatars, --gps, or --purge-cafes (or --list-polluted-cafes)")

    purge_ids = []
    if args.purge_cafes:
        with open(args.purge_cafes) as f:
            purge_ids = [ln.strip() for ln in f if ln.strip()]

    mode = "APPLY (deleting)" if args.apply else "DRY-RUN (no changes)"
    print(f"=== clean_google_images: {mode}, db={args.db} ===")
    tot_rows = tot_files = 0
    for db in open_targets(args.db):
        if args.avatars:
            r, f = phase_avatars(db, args.apply)
            tot_rows += r
            tot_files += f
        if args.gps:
            r, f = phase_gps(db, args.gps_meters, args.apply)
            tot_rows += r
            tot_files += f
        if args.purge_cafes:
            r, d = phase_purge(db, purge_ids, args.apply, args.delete_files)
            tot_rows += r
            tot_files += d
        db.close()
    print(f"=== total: {tot_rows} rows, {tot_files} files "
          f"{'deleted' if args.apply else 'to delete'} ===")
    if not args.apply:
        print("Re-run with --apply to execute.")


if __name__ == "__main__":
    main()
