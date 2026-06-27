#!/usr/bin/env python3
"""
RAM+ image tagger — tags cafe images using Recognize Anything Plus Model (4585 classes).
Saves tags to image_tags table (same schema as YOLO tagger, no boxes).

Weights auto-downloaded on first run (~700MB swin_base or ~2.3GB swin_large).

Run from project root:
    SNAPSHOT=$(python scripts/create_tag_snapshot.py --n 100 | tail -1)
    python scripts/tag_images_ram.py --from-db "$SNAPSHOT"

Or via Justfile:
    just tag-images-ram 100
"""

import argparse, json, os, sqlite3, time
from datetime import datetime, timezone
from pathlib import Path

TAGGER = "ram_plus_v3"  # bump when TAG_FILTER or scoring logic changes

DATA_DIR        = "data/seoul"
BATCH_SIZE      = 8   # swin_large BERT head needs lots of memory; 8 fits 4GB VRAM
DB_WRITE_RETRIES = 10
DB_WRITE_PAUSE   = 30  # seconds between retries on DB lock

# RAM+ weights — only swin_large available (~2.3GB, fits 4GB VRAM when not running YOLO)
# Regular RAM (less accurate) also available as fallback
MODEL_URLS = {
    "swin_large":      "https://huggingface.co/xinyu1205/recognize-anything-plus-model/resolve/main/ram_plus_swin_large_14m.pth",
    "ram_swin_large":  "https://huggingface.co/xinyu1205/recognize_anything_model/resolve/main/ram_swin_large_14m.pth",
}
MODEL_FILES = {
    "swin_large":     "ram_plus_swin_large_14m.pth",
    "ram_swin_large": "ram_swin_large_14m.pth",
}
DEFAULT_VIT = "swin_large"

# Tags to keep from RAM's 4585 classes (substring match, case-insensitive).
# Order matters: exact matches checked first, then substring. Put specific before general.
# None = skip explicitly (prevents substring fallthrough).
TAG_FILTER: dict[str, str | None] = {
    # Seating
    "bar stool":      "bar stool",
    "stool":          "stool",
    "armchair":       "armchair",
    "office chair":   "office chair",
    "swivel chair":   "swivel chair",
    "computer chair": "computer chair",
    "folding chair":  "folding chair",
    "chair":          "chair",
    "sofa":           "sofa",
    "couch":          "couch",
    "bench":          "bench",
    "loveseat":       "loveseat",
    # Tables / workspace
    "computer desk":  "computer desk",
    "writing desk":   "writing desk",
    "office desk":    "office desk",
    "desk":           "desk",
    "dining table":   "dining table",
    "dinning table":  "dining table",
    "round table":    "round table",
    "glass table":    "glass table",
    "coffee table":   "coffee table",
    "table":          "table",
    # Power
    "electric outlet":         "power outlet",
    "power plugs and sockets": "power outlet",
    "extension cord":          "extension cord",
    "charger":                 "charger",
    "socket":                  "power outlet",
    # Devices
    "laptop":          "laptop",
    "laptop keyboard": "laptop",
    "notebook":        "laptop",
    "tablet computer": "tablet",
    "computer":        "computer",
    # Space / windows
    "glass window": "window",
    "window":       "window",
    # Food — specific before general to avoid wrong substring matches
    "chocolate chip cookie": "cookie",
    "birthday cake":  "cake",
    "carrot cake":    "cake",
    "cheesecake":     "cheesecake",
    "fruit cake":     "cake",
    "moon cake":      "cake",
    "wedding cake":   "cake",
    "cake":           "cake",
    "egg tart":       "tart",
    "tart":           "tart",
    "croissant":      "croissant",
    "bagel":          "bagel",
    "banana bread":   "bread",
    "cornbread":      "bread",
    "pocket bread":   "bread",
    "steamed bread":  "bread",
    "bread":          "bread",
    "waffle":         "waffle",
    "pancake":        "pancake",
    "french toast":   "toast",
    "toast":          "toast",
    "scone":          "scone",
    "muffin":         "muffin",
    "cupcake":        "cupcake",
    "gingerbread":    "bread",
    "pastry":         "pastry",
    "ice cream cone": "ice cream",
    "ice cream":      "ice cream",
    "dessert":        "dessert",
    "sandwich":       "sandwich",
    "submarine sandwich": "sandwich",
    "chicken salad":  "salad",
    "fruit salad":    "salad",
    "potato salad":   "salad",
    "salad":          "salad",
    "brunch":         "brunch",
    "breakfast":      "breakfast",
    "comfort food":   "food",
    "fast food":      None,
    "street food":    None,
    "food":           "food",
    # Drinks
    "platter":        None,   # blocks "latte" in "platter" false positive
    "latte":          "latte",
    "espresso":       "espresso",
    "coffee cup":     "coffee",
    "coffeepot":      "coffee",
    "coffee":         "coffee",
    "apple juice":    "juice",
    "lemon juice":    "juice",
    "orange juice":   "juice",
    "juice":          "juice",
    "beer glass":     "beer",
    "beer bottle":    "beer",
    "beer":           "beer",
    "red wine":       "wine",
    "white wine":     "wine",
    "sparkling wine": "wine",
    "wine glass":     "wine",
    "wine":           "wine",
    "drink":          "drink",
    # Ambiance / space
    "terrace":        "terrace",
    "balcony":        "balcony",
    "roof garden":    "rooftop",
    "art gallery":    "art",
    "art exhibition": "art",
    "art studio":     "art",
    "record player":  "vinyl",
    "recording studio": None,
    "record":         "vinyl",
    "bookstore":      "bookstore",
    "bookcase":       "bookshelf",
    "bookshelf":      "bookshelf",
    # Plants — specific first to prevent eggplant/houseplant false matches
    "eggplant":       None,
    "pitcher plant":  None,
    "plantation":     None,
    "houseplant":     "plant",
    "indoor plant":   "plant",
    "plant":          "plant",
    # Amenities
    "parking lot":    "parking",
    "parking garage": "parking",
    "parking":        "parking",
    "restroom":       "restroom",
    "toilet bowl":    None,
    "toilet":         None,
    # Pets
    "french bulldog": "dog",
    "bulldog":        "dog",
    "persian cat":    "cat",
    "street dog":     None,
    "dog food":       None,
    "cat food":       None,
    "catch":          None,   # blocks "cat" in "catch" false positive
    "scatter":        None,   # blocks "cat" in "scatter" false positive
    "dog":            "dog",
    "cat":            "cat",
    # Activities
    "board game":     "board game",
    # Scene type
    "interior":           "interior",
    "building exterior":  "building exterior",  # must precede "exterior"
    "outdoor":            "exterior",
    "exterior":           "exterior",
    "building":           "building exterior",
    "facade":             "building exterior",
    "street view":        "street view",
    "street art":         None,
    "street artist":      None,
    "street":             "street view",
    # Misc
    "barista":        "barista",
    "menu":           "menu visible",
}


def _parse_args():
    p = argparse.ArgumentParser(description="RAM+ image tagger")
    p.add_argument("--from-db",  required=True, dest="db")
    p.add_argument("--n",        default="all",   help="Cafes to process (int or 'all')")
    p.add_argument("--vit",      default=DEFAULT_VIT, choices=["swin_large", "ram_swin_large"])
    p.add_argument("--threshold",type=float, default=0.68, help="RAM confidence threshold (default 0.68)")
    p.add_argument("--rollup-every", type=int, default=500, dest="rollup_every")
    return p.parse_args()


def local_to_disk(local_path: str) -> str:
    stripped = local_path.removeprefix("/images")
    return os.path.join(DATA_DIR, stripped.lstrip("/"))


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS image_tags (
            image_id INTEGER NOT NULL REFERENCES images(id),
            tag      TEXT    NOT NULL,
            score    REAL    NOT NULL DEFAULT 1.0,
            boxes    TEXT,
            PRIMARY KEY (image_id, tag)
        );
        CREATE INDEX IF NOT EXISTS idx_image_tags_tag   ON image_tags(tag);
        CREATE INDEX IF NOT EXISTS idx_image_tags_image ON image_tags(image_id);
    """)
    for col in ("boxes TEXT", "tagged_at TEXT", "tagger TEXT"):
        try:
            conn.execute(f"ALTER TABLE image_tags ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass
    try:
        conn.execute("ALTER TABLE clean_cafes ADD COLUMN tags TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE images ADD COLUMN tagged_at TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()


def _write_with_retry(conn: sqlite3.Connection, fn) -> None:
    """Run fn(conn) with up to DB_WRITE_RETRIES retries on SQLite lock errors."""
    Y = "\033[33m"; NC = "\033[0m"
    for attempt in range(1, DB_WRITE_RETRIES + 1):
        try:
            fn(conn)
            return
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower():
                raise
            try:
                conn.rollback()
            except Exception:
                pass
            print(f"\n{Y}WARNING: DB locked — {e}. Attempt {attempt}/{DB_WRITE_RETRIES}. Retrying in {DB_WRITE_PAUSE}s...{NC}", flush=True)
            time.sleep(DB_WRITE_PAUSE)
    raise sqlite3.OperationalError(f"DB still locked after {DB_WRITE_RETRIES} retries ({DB_WRITE_PAUSE}s each)")


def sample_images(conn: sqlite3.Connection, n_cafes: int | None) -> list[tuple]:
    # Order: never-tagged first (priority=0), then old-tagger (priority=1).
    # Images already tagged by current TAGGER are excluded entirely.
    base_filter = "file_size > 0 AND local_path IS NOT NULL AND local_path != ''"
    order = """
        ORDER BY
            CASE WHEN id NOT IN (SELECT DISTINCT image_id FROM image_tags) THEN 0 ELSE 1 END,
            RANDOM()
    """
    exclude_current = f"id NOT IN (SELECT DISTINCT image_id FROM image_tags WHERE tagger = '{TAGGER}')"
    if n_cafes is None:
        return conn.execute(f"""
            SELECT id, cafe_id, local_path FROM images
            WHERE {base_filter} AND {exclude_current}
            {order}
        """).fetchall()
    return conn.execute(f"""
        SELECT i.id, i.cafe_id, i.local_path
        FROM images i
        JOIN (
            SELECT cafe_id FROM images
            WHERE {base_filter}
            GROUP BY cafe_id ORDER BY COUNT(*) DESC LIMIT ?
        ) top ON i.cafe_id = top.cafe_id
        WHERE {base_filter} AND {exclude_current}
        {order}
    """, (n_cafes,)).fetchall()


def already_tagged(conn: sqlite3.Connection, image_id: int) -> bool:
    # Only skip if tagged by the current tagger version — older versions get re-tagged
    return conn.execute(
        "SELECT 1 FROM image_tags WHERE image_id = ? AND tagger = ? LIMIT 1",
        (image_id, TAGGER)
    ).fetchone() is not None


def rollup(conn: sqlite3.Connection) -> None:
    rows = conn.execute("""
        SELECT c.belongs_to_cafe_id, it.tag, COUNT(*) as cnt
        FROM image_tags it
        JOIN images i ON i.id = it.image_id
        JOIN scraped_cafes c ON c.id = i.cafe_id
        WHERE c.belongs_to_cafe_id IS NOT NULL
        GROUP BY c.belongs_to_cafe_id, it.tag
    """).fetchall()
    cafe_tags: dict[str, dict[str, int]] = {}
    for cafe_id, tag, cnt in rows:
        cafe_tags.setdefault(cafe_id, {})[tag] = cnt
    update_rows = [(json.dumps(dict(sorted(t.items(), key=lambda x: -x[1]))), cid)
                   for cid, t in cafe_tags.items()]
    _write_with_retry(conn, lambda c: (
        c.executemany("UPDATE clean_cafes SET tags = ? WHERE id = ?", update_rows),
        c.commit(),
    ))


def build_tag_filter(all_ram_tags: list[str]) -> dict[int, str]:
    """Map RAM tag index → our normalized tag name, using TAG_FILTER patterns."""
    result: dict[int, str] = {}
    for idx, ram_tag in enumerate(all_ram_tags):
        lower = ram_tag.lower()
        for pattern, mapped in TAG_FILTER.items():
            if pattern.lower() == lower:  # exact match first
                if mapped is not None:
                    result[idx] = mapped
                break
        else:
            for pattern, mapped in TAG_FILTER.items():
                if pattern.lower() in lower:
                    if mapped is not None:
                        result.setdefault(idx, mapped)
                    break
    return result


def get_weights(vit: str) -> str:
    fname = MODEL_FILES[vit]
    if os.path.exists(fname):
        return fname
    url = MODEL_URLS[vit]
    print(f"Downloading weights from {url} ...")
    import urllib.request
    def _progress(count, block, total):
        mb = count * block // 1024 // 1024
        total_mb = total // 1024 // 1024
        pct = min(100, count * block / total * 100)
        print(f"  {pct:.1f}%  ({mb}/{total_mb} MB)", end="\r")
    urllib.request.urlretrieve(url, fname, _progress)
    print(f"\nSaved: {fname}")
    return fname


def run() -> None:
    args = _parse_args()

    try:
        import torch
        from PIL import Image, UnidentifiedImageError
        from ram.models import ram_plus
        from ram import get_transform
    except ImportError as e:
        print(f"Missing dependency: {e}")
        print("Install: pip install git+https://github.com/xinyu1205/recognize-anything timm fairscale")
        raise SystemExit(1)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Device: {device}")

    weights = get_weights(args.vit)
    print(f"Loading RAM+ ({args.vit}) from {weights}...")

    import warnings
    vit_arg = "swin_l" if args.vit == "swin_large" else "swin_b"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = ram_plus(
            pretrained=weights,
            image_size=384,
            vit=vit_arg,
            threshold=args.threshold,
        )
    model.eval()
    if device == "cuda":
        model = model.half()  # fp16: ~1.6GB vs 3.2GB float32
    model = model.to(device)

    transform = get_transform(image_size=384)

    # model.tag_list is a numpy array of tag strings
    ram_tag_list = [t.strip() for t in model.tag_list.tolist()]
    tag_filter = build_tag_filter(ram_tag_list)
    print(f"RAM tags: {len(ram_tag_list)} total, {len(tag_filter)} mapped to our vocab")
    print("Mapped classes:")
    seen: set[str] = set()
    for idx, name in sorted(tag_filter.items(), key=lambda x: x[1]):
        if name not in seen:
            print(f"  {ram_tag_list[idx]!r:35s} → {name!r}")
            seen.add(name)

    conn = sqlite3.connect(args.db, timeout=120)
    migrate(conn)

    # ── Startup banner ────────────────────────────────────────────────────────
    B = "\033[1m"; C = "\033[36m"; G = "\033[32m"; Y = "\033[33m"; R = "\033[31m"; NC = "\033[0m"
    print(f"\n{B}{'━'*60}{NC}")
    print(f"{B}{C}  RAM+ Image Tagger  {Y}{TAGGER}{NC}")
    print(f"{B}{'━'*60}{NC}")
    print(f"  Model     : {args.vit}")
    print(f"  DB        : {args.db}")
    print(f"  Threshold : {args.threshold}")
    print(f"  Tags      : {len(set(v for v in TAG_FILTER.values() if v))} unique → {sorted(set(v for v in TAG_FILTER.values() if v))}")

    history = conn.execute("""
        SELECT tagger, COUNT(DISTINCT image_id) as images, COUNT(*) as tag_rows,
               MIN(tagged_at) as first, MAX(tagged_at) as last
        FROM image_tags
        GROUP BY tagger ORDER BY first
    """).fetchall()
    if history:
        print(f"\n{B}  Tagging history:{NC}")
        for tagger, imgs, tag_rows, first, last in history:
            label = f"{Y}{tagger}{NC}" if tagger else f"{R}(unknown version){NC}"
            print(f"    {label:<40}  {imgs:>6} images  {tag_rows:>7} tag rows  {first or '?'} → {last or '?'}")
    else:
        print(f"\n  {Y}No existing tag data in this DB.{NC}")
    print(f"{B}{'━'*60}{NC}\n")
    # ─────────────────────────────────────────────────────────────────────────

    n_cafes = None if args.n == "all" else int(args.n)
    images = sample_images(conn, n_cafes)

    total_all      = conn.execute(
        "SELECT COUNT(*) FROM images WHERE file_size > 0 AND local_path IS NOT NULL AND local_path != ''"
    ).fetchone()[0]
    tagged_any     = conn.execute(
        "SELECT COUNT(DISTINCT image_id) FROM image_tags"
    ).fetchone()[0]
    tagged_current = conn.execute(
        "SELECT COUNT(DISTINCT image_id) FROM image_tags WHERE tagger = ?", (TAGGER,)
    ).fetchone()[0]
    never_tagged   = total_all - tagged_any
    old_tagger     = tagged_any - tagged_current
    queued         = len(images)

    def _pct(n): return f"{100*n/total_all:.1f}%" if total_all else "0%"

    print(f"  {'Total on disk:':<28} {total_all:>8,}")
    print(f"  {G}{'Never tagged:':<28}{NC} {never_tagged:>8,}  {G}{_pct(never_tagged)}{NC}")
    print(f"  {Y}{'Tagged (older version):':<28}{NC} {old_tagger:>8,}  {Y}{_pct(old_tagger)}{NC}")
    print(f"  {G}{'Done ({}):':<28}{NC} {tagged_current:>8,}  {G}{_pct(tagged_current)}{NC}".format(TAGGER))
    print(f"  {'Queued this run:':<28} {queued:>8,}  ({_pct(never_tagged)} untagged + {_pct(old_tagger)} old)")

    total = queued  # progress bar denominator

    def _bar(done_this_run: int, elapsed: float, bar_len: int = 30) -> str:
        pct = done_this_run / queued if queued > 0 else 0
        filled = int(bar_len * pct)
        bar = "█" * filled + "░" * (bar_len - filled)
        eta = ""
        if done_this_run > 0 and elapsed > 0:
            rate = done_this_run / elapsed
            secs_left = (queued - done_this_run) / rate
            if secs_left > 0:
                h, rem = divmod(int(secs_left), 3600)
                m, s = divmod(rem, 60)
                eta = f"  ETA {f'{h}h' if h else ''}{m}m{s:02d}s"
        rate_str = f"  {done_this_run/elapsed:.1f} img/s" if elapsed > 0 else ""
        return f"[{bar}] {done_this_run}/{queued} ({pct*100:.1f}%){rate_str}{eta}"

    skipped = tagged = failed = 0
    tag_counts: dict[str, int] = {}
    t_start = time.perf_counter()
    times_per_img: list[float] = []
    batches_since_checkpoint = 0

    for batch_start in range(0, total, BATCH_SIZE):
        batch = images[batch_start : batch_start + BATCH_SIZE]

        tensors: list = []
        valid: list[int] = []

        for image_id, _cafe_id, local_path in batch:
            if already_tagged(conn, image_id):
                skipped += 1
                continue
            disk_path = local_to_disk(local_path)
            try:
                img = Image.open(disk_path).convert("RGB")
                tensors.append(transform(img))
                valid.append(image_id)
            except (FileNotFoundError, UnidentifiedImageError, OSError):
                failed += 1

        if not tensors:
            elapsed = time.perf_counter() - t_start
            print(f"\r{_bar(tagged, elapsed)}  skip={skipped} fail={failed}", end="", flush=True)
            continue

        import torch
        batch_tensor = torch.stack(tensors).to(device)
        if device == "cuda":
            batch_tensor = batch_tensor.half()

        t0 = time.perf_counter()
        with torch.no_grad():
            # RAM+ returns (tag_string, tag_string_chinese) per image in batch
            tag_output = model.generate_tag(batch_tensor)
        dt = time.perf_counter() - t0
        times_per_img.extend([dt / len(tensors)] * len(tensors))

        rows: list[tuple] = []
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        for image_id, tags_str in zip(valid, tag_output[0]):
            # tags_str is like "chair | table | window"
            raw_tags = [t.strip().lower() for t in tags_str.split("|") if t.strip()]
            for raw in raw_tags:
                # Find matching mapped tag
                for pattern, mapped in TAG_FILTER.items():
                    if pattern.lower() == raw or pattern.lower() in raw:
                        if mapped is not None:
                            rows.append((image_id, mapped, 1.0, None, now, TAGGER))
                            tag_counts[mapped] = tag_counts.get(mapped, 0) + 1
                        break
            tagged += 1

        if rows:
            _write_with_retry(conn, lambda c: (
                c.executemany(
                    "INSERT OR REPLACE INTO image_tags (image_id, tag, score, boxes, tagged_at, tagger) VALUES (?, ?, ?, ?, ?, ?)",
                    rows,
                ),
                c.commit(),
            ))
            batches_since_checkpoint += 1
            if batches_since_checkpoint >= 100:
                conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                batches_since_checkpoint = 0

        # Mark all processed images as tagged (even if 0 tags passed filter)
        _write_with_retry(conn, lambda c, ids=list(valid), ts=now: (
            c.executemany(
                "UPDATE images SET tagged_at = ? WHERE id = ? AND tagged_at IS NULL",
                [(ts, iid) for iid in ids],
            ),
            c.commit(),
        ))

        if args.rollup_every > 0 and tagged > 0 and tagged % args.rollup_every < BATCH_SIZE:
            rollup(conn)

        elapsed = time.perf_counter() - t_start
        print(f"\r{_bar(tagged, elapsed)}  skip={skipped} fail={failed}", end="", flush=True)

    total_elapsed = time.perf_counter() - t_start
    avg_ips = tagged / total_elapsed if total_elapsed > 0 else 0
    avg_ms = (sum(times_per_img) / len(times_per_img) * 1000) if times_per_img else 0

    print(f"\n\nDone. tagged={tagged} skipped={skipped} failed={failed}")
    print(f"Throughput: {avg_ips:.1f} img/s  avg latency: {avg_ms:.1f} ms/img")

    if args.rollup_every > 0:
        rollup(conn)
        print("Rollup complete.")

    n_tag_rows = conn.execute("SELECT COUNT(*) FROM image_tags").fetchone()[0]
    print(f"Total tag rows: {n_tag_rows}")

    print("\nTag distribution:")
    dist = conn.execute("""
        SELECT tag, COUNT(*) as cnt FROM image_tags GROUP BY tag ORDER BY cnt DESC
    """).fetchall()
    for tag, cnt in dist:
        print(f"  {tag:<30} {cnt:5d} images")

    # Write benchmark to .md
    db_path = Path(args.db)
    md_path = db_path.with_suffix(".md")
    tag_lines = "\n".join(f"- {tag}: {cnt} imgs" for tag, cnt in dist)
    bench = f"""
## RAM+ Tagging Results

- Model: RAM+ {args.vit}
- Confidence threshold: {args.threshold}
- Images processed: {tagged}
- Throughput: {avg_ips:.1f} img/s, avg {avg_ms:.1f} ms/img
- Tag rows written: {n_tag_rows}

### Tag Distribution
{tag_lines}
"""
    if md_path.exists():
        existing = md_path.read_text()
        if "## RAM+ Tagging" in existing:
            existing = existing[:existing.index("## RAM+ Tagging")]
        md_path.write_text(existing.rstrip() + "\n" + bench)
    else:
        md_path.write_text(f"# RAM+ Tagging\n\n- DB: `{args.db}`\n" + bench)

    print(f"Benchmark written to {md_path}")
    conn.close()


if __name__ == "__main__":
    run()
