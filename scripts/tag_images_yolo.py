#!/usr/bin/env python3
"""
YOLOv8 image tagger — tags cafe images using YOLOv8 OIV7 (600 Open Images classes).
Saves tag + bounding boxes to image_tags table.

Run from project root:
    SNAPSHOT=$(python scripts/create_tag_snapshot.py --n 100 | tail -1)
    python scripts/tag_images_yolo.py --from-db "$SNAPSHOT"

Or via Justfile:
    just tag-images-yolo 100 0.25
"""

import argparse, json, os, sqlite3, time
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
DATA_DIR   = "data/seoul"
MODEL_NAME = "yolov8x-oiv7.pt"   # auto-downloaded ~130 MB on first run
BATCH_SIZE = 16

# OIV7 class name substrings (case-insensitive) → our tag name.
# None = skip class entirely.
TAG_MAP: dict[str, str | None] = {
    "wall socket":  "wall socket",
    "power strip":  "power strip",
    "plug":         "plug",
    "laptop":       "laptop",
    "chair":        "chair",
    "table":        "table",
    "coffee cup":   "cup",
    "cup":          "cup",
    "food":         "food",
    "window":       "window",
    "person":       None,   # skip — privacy
}
# ─────────────────────────────────────────────────────────────────────────────


def _parse_args():
    p = argparse.ArgumentParser(description="YOLOv8 image tagger")
    p.add_argument("--from-db", required=True, dest="db", help="Snapshot DB to tag (from create_tag_snapshot.py)")
    p.add_argument("--n",     default="all",            help="Number of cafes (int or 'all')")
    p.add_argument("--conf",  type=float, default=0.25, help="Min detection confidence (default 0.25)")
    p.add_argument("--model", default=MODEL_NAME,       help="YOLOv8 model file or name")
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
    # Idempotent: add boxes column to existing DBs
    try:
        conn.execute("ALTER TABLE image_tags ADD COLUMN boxes TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    conn.commit()


def sample_images(conn: sqlite3.Connection, n_cafes: int | None) -> list[tuple]:
    if n_cafes is None:
        return conn.execute("""
            SELECT id, cafe_id, local_path FROM images
            WHERE file_size > 0 AND local_path IS NOT NULL AND local_path != ''
            ORDER BY cafe_id
        """).fetchall()
    return conn.execute("""
        SELECT i.id, i.cafe_id, i.local_path
        FROM images i
        JOIN (
            SELECT cafe_id FROM images
            WHERE file_size > 0 AND local_path IS NOT NULL AND local_path != ''
            GROUP BY cafe_id ORDER BY COUNT(*) DESC LIMIT ?
        ) top ON i.cafe_id = top.cafe_id
        WHERE i.file_size > 0 AND i.local_path IS NOT NULL AND i.local_path != ''
        ORDER BY i.cafe_id
    """, (n_cafes,)).fetchall()


def already_tagged(conn: sqlite3.Connection, image_id: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM image_tags WHERE image_id = ? LIMIT 1", (image_id,)
    ).fetchone() is not None


def build_class_filter(model_names: dict[int, str]) -> dict[int, str]:
    """Map model class indices → our tag name, skipping None entries."""
    result: dict[int, str] = {}
    for cls_idx, cls_name in model_names.items():
        lower = cls_name.lower()
        for pattern, tag in TAG_MAP.items():
            if pattern in lower:
                if tag is not None:
                    result[cls_idx] = tag
                break
    return result


def run() -> None:
    args = _parse_args()

    try:
        from ultralytics import YOLO
        from PIL import Image, UnidentifiedImageError
    except ImportError as e:
        print(f"Missing dependency: {e}")
        print("Install: pip install ultralytics pillow")
        raise SystemExit(1)

    import torch
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Device: {device}")
    print(f"Loading model {args.model}...")

    model = YOLO(args.model)
    cls_filter = build_class_filter(model.names)
    print(f"Detected {len(cls_filter)} relevant classes:")
    for idx, tag in sorted(cls_filter.items(), key=lambda x: x[1]):
        print(f"  [{idx:3d}] {model.names[idx]!r:30s} → {tag!r}")

    conn = sqlite3.connect(args.db)
    migrate(conn)

    n_cafes = None if args.n == "all" else int(args.n)
    images = sample_images(conn, n_cafes)
    total = len(images)
    print(f"\nImages to process: {total}")
    print(f"Confidence threshold: {args.conf}")

    skipped = tagged = failed = 0
    tag_counts: dict[str, int] = {}
    t_start = time.perf_counter()
    times_per_img: list[float] = []

    for batch_start in range(0, total, BATCH_SIZE):
        batch = images[batch_start : batch_start + BATCH_SIZE]

        pil_images: list = []
        valid: list[tuple] = []

        for image_id, cafe_id, local_path in batch:
            if already_tagged(conn, image_id):
                skipped += 1
                continue
            disk_path = local_to_disk(local_path)
            try:
                img = Image.open(disk_path).convert("RGB")
                pil_images.append(img)
                valid.append((image_id, img.width, img.height))
            except (FileNotFoundError, UnidentifiedImageError, OSError):
                failed += 1

        if not pil_images:
            continue

        t0 = time.perf_counter()
        results = model(pil_images, conf=args.conf, verbose=False, device=device)
        dt = time.perf_counter() - t0
        times_per_img.extend([dt / len(pil_images)] * len(pil_images))

        rows: list[tuple] = []
        for (image_id, img_w, img_h), result in zip(valid, results):
            # Group detections by our tag name
            tag_boxes: dict[str, list[list[float]]] = {}
            tag_scores: dict[str, float] = {}

            if result.boxes is not None and len(result.boxes):
                for box in result.boxes:
                    cls_idx = int(box.cls.item())
                    if cls_idx not in cls_filter:
                        continue
                    tag_name = cls_filter[cls_idx]
                    conf_val = float(box.conf.item())
                    # xyxyn = normalized [x1, y1, x2, y2]
                    x1, y1, x2, y2 = box.xyxyn[0].tolist()
                    box_coord = [round(x1, 4), round(y1, 4), round(x2, 4), round(y2, 4)]
                    tag_boxes.setdefault(tag_name, []).append(box_coord)
                    # Keep max confidence for score
                    tag_scores[tag_name] = max(tag_scores.get(tag_name, 0.0), conf_val)

            for tag_name, boxes in tag_boxes.items():
                rows.append((image_id, tag_name, tag_scores[tag_name], json.dumps(boxes)))
                tag_counts[tag_name] = tag_counts.get(tag_name, 0) + 1

            tagged += 1

        if rows:
            conn.executemany(
                "INSERT OR REPLACE INTO image_tags (image_id, tag, score, boxes) VALUES (?, ?, ?, ?)",
                rows,
            )
            conn.commit()

        done = batch_start + len(batch)
        elapsed = time.perf_counter() - t_start
        ips = tagged / elapsed if elapsed > 0 else 0
        print(f"  [{done:5d}/{total}] tagged={tagged} skipped={skipped} failed={failed}  {ips:.1f} img/s", end="\r")

    total_elapsed = time.perf_counter() - t_start
    avg_ips = tagged / total_elapsed if total_elapsed > 0 else 0
    avg_ms = (sum(times_per_img) / len(times_per_img) * 1000) if times_per_img else 0

    print(f"\n\nDone. tagged={tagged} skipped={skipped} failed={failed}")
    print(f"Throughput: {avg_ips:.1f} img/s  avg latency: {avg_ms:.1f} ms/img")

    n_tag_rows = conn.execute("SELECT COUNT(*) FROM image_tags").fetchone()[0]
    n_with_boxes = conn.execute("SELECT COUNT(*) FROM image_tags WHERE boxes IS NOT NULL").fetchone()[0]
    print(f"Total tag rows: {n_tag_rows}  ({n_with_boxes} with bounding boxes)")

    print("\nTag distribution:")
    dist = conn.execute("""
        SELECT tag, COUNT(*) as cnt, ROUND(AVG(score), 3) as avg_score
        FROM image_tags GROUP BY tag ORDER BY cnt DESC
    """).fetchall()
    for tag, cnt, avg_s in dist:
        print(f"  {tag:<25} {cnt:5d} images  avg_score={avg_s}")

    # Write benchmark to .md file beside the DB
    db_path = Path(args.db)
    md_path = db_path.with_suffix(".md")
    tag_lines = "\n".join(f"- {tag}: {cnt} imgs (avg score {avg_s})" for tag, cnt, avg_s in dist)
    bench_section = f"""
## YOLOv8 Tagging Results

- Model: `{args.model}`
- Confidence threshold: {args.conf}
- Images processed: {tagged}
- Throughput: {avg_ips:.1f} img/s, avg {avg_ms:.1f} ms/img
- Tag rows written: {n_tag_rows} ({n_with_boxes} with bounding boxes)

### Tag Distribution
{tag_lines}
"""

    # Append to existing .md or create new one
    if md_path.exists():
        existing = md_path.read_text()
        # Remove old benchmark section if present
        if "## YOLOv8 Tagging Benchmark" in existing:
            existing = existing[:existing.index("## YOLOv8 Tagging Benchmark")]
        md_path.write_text(existing.rstrip() + "\n" + bench_section)
    else:
        md_path.write_text(f"# YOLO Tagging Experiment\n\n- **DB**: `{args.db}`\n" + bench_section)

    print(f"\nBenchmark written to {md_path}")
    conn.close()


if __name__ == "__main__":
    run()
