#!/usr/bin/env python3
"""
CLIP image tagger — tags cafe images, stores results in image_tags table.

Run from project root:
    python scripts/tag_images_clip.py

Config:
"""

import argparse, os, sys, sqlite3, json, time
from datetime import datetime, timezone
from pathlib import Path

TAGGER = "clip_v1"  # bump when TAGS list or scoring logic changes

# ── Config ────────────────────────────────────────────────────────────────────
SCRAPED_DB   = "data/seoul/clean.db"
DATA_DIR     = "data/seoul"       # local_path prefix strip: /images/X → DATA_DIR/X
BATCH_SIZE   = 32                 # images per CLIP forward pass
MODEL_NAME   = "openai/clip-vit-base-patch32"

def _parse_args():
    p = argparse.ArgumentParser(description="CLIP image tagger")
    p.add_argument("--n", default="100",
                   help="Number of cafes to sample (integer or 'all')")
    p.add_argument("--threshold", type=float, default=0.22,
                   help="Min cosine similarity to store a tag (default 0.22)")
    p.add_argument("--db", default=SCRAPED_DB,
                   help="Target SQLite DB path")
    return p.parse_args()

args = _parse_args()
THRESHOLD    = args.threshold
SCRAPED_DB   = args.db
SAMPLE_CAFES = None if args.n == "all" else int(args.n)

TAGS = [
    "chair",
    "tall chair",
    "bar chair",
    "table",
    "large table",
    "high table",
    "power plug",
    "wall plug",
    "electric plug"
    "interior",
    "exterior",
    "food",
    "window",
    "laptop",
    "large space",
    "small space",
    "salty food",
    "sweet food",
    "tea"
]
# ─────────────────────────────────────────────────────────────────────────────


def local_to_disk(local_path: str) -> str:
    # /images/naver/1234/photo.jpg → data/seoul/naver/1234/photo.jpg
    stripped = local_path.removeprefix("/images")  # → /naver/1234/photo.jpg
    return os.path.join(DATA_DIR, stripped.lstrip("/"))


def migrate(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS image_tags (
            image_id INTEGER NOT NULL REFERENCES images(id),
            tag      TEXT    NOT NULL,
            score    REAL    NOT NULL DEFAULT 1.0,
            PRIMARY KEY (image_id, tag)
        );
        CREATE INDEX IF NOT EXISTS idx_image_tags_tag ON image_tags(tag);
        CREATE INDEX IF NOT EXISTS idx_image_tags_image ON image_tags(image_id);
    """)
    for col in ("boxes TEXT", "tagged_at TEXT", "tagger TEXT"):
        try:
            conn.execute(f"ALTER TABLE image_tags ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass
    conn.commit()


def sample_images(conn: sqlite3.Connection) -> list[tuple]:
    """Return (id, cafe_id, local_path) for images in top SAMPLE_CAFES cafes."""
    if SAMPLE_CAFES is None:
        rows = conn.execute("""
            SELECT id, cafe_id, local_path FROM images
            WHERE file_size > 0 AND local_path IS NOT NULL AND local_path != ''
            ORDER BY cafe_id
        """).fetchall()
    else:
        rows = conn.execute("""
            SELECT i.id, i.cafe_id, i.local_path
            FROM images i
            JOIN (
                SELECT cafe_id
                FROM images
                WHERE file_size > 0 AND local_path IS NOT NULL AND local_path != ''
                GROUP BY cafe_id
                ORDER BY COUNT(*) DESC
                LIMIT ?
            ) top ON i.cafe_id = top.cafe_id
            WHERE i.file_size > 0 AND i.local_path IS NOT NULL AND i.local_path != ''
            ORDER BY i.cafe_id
        """, (SAMPLE_CAFES,)).fetchall()
    return rows


def already_tagged(conn: sqlite3.Connection, image_id: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM image_tags WHERE image_id = ? LIMIT 1", (image_id,)
    ).fetchone() is not None


def run():
    import torch
    from PIL import Image, UnidentifiedImageError
    from transformers import CLIPProcessor, CLIPModel

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Device: {device}")

    print(f"Loading model {MODEL_NAME}...")
    model = CLIPModel.from_pretrained(MODEL_NAME).to(device)
    processor = CLIPProcessor.from_pretrained(MODEL_NAME)
    model.eval()

    # Pre-compute text features for all tags
    text_inputs = processor(
        text=[f"a photo showing {t}" for t in TAGS],
        return_tensors="pt", padding=True
    ).to(device)
    with torch.no_grad():
        text_out = model.text_model(**text_inputs)
        text_features = model.text_projection(text_out.pooler_output)
        text_features = text_features / text_features.norm(dim=-1, keepdim=True)

    conn = sqlite3.connect(SCRAPED_DB)
    migrate(conn)

    images = sample_images(conn)
    total = len(images)
    print(f"Images to tag: {total}")

    skipped = 0
    failed = 0
    tagged = 0

    for batch_start in range(0, total, BATCH_SIZE):
        batch = images[batch_start : batch_start + BATCH_SIZE]

        pil_images = []
        valid = []
        for image_id, cafe_id, local_path in batch:
            if already_tagged(conn, image_id):
                skipped += 1
                continue
            disk_path = local_to_disk(local_path)
            try:
                img = Image.open(disk_path).convert("RGB")
                pil_images.append(img)
                valid.append((image_id, cafe_id))
            except (FileNotFoundError, UnidentifiedImageError, Exception):
                failed += 1

        if not pil_images:
            continue

        inputs = processor(images=pil_images, return_tensors="pt").to(device)
        with torch.no_grad():
            image_out = model.vision_model(**inputs)
            image_features = model.visual_projection(image_out.pooler_output)
            image_features = image_features / image_features.norm(dim=-1, keepdim=True)
            scores = (image_features @ text_features.T).cpu().numpy()  # (N, T)

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        rows = []
        for (image_id, _), score_row in zip(valid, scores):
            for tag_idx, score in enumerate(score_row):
                if float(score) >= THRESHOLD:
                    rows.append((image_id, TAGS[tag_idx], float(score), now, TAGGER))
            tagged += 1

        if rows:
            conn.executemany(
                "INSERT OR REPLACE INTO image_tags (image_id, tag, score, tagged_at, tagger) VALUES (?, ?, ?, ?, ?)",
                rows
            )
            conn.commit()

        done = batch_start + len(batch)
        print(f"  [{done}/{total}] tagged={tagged} skipped={skipped} failed={failed}", end="\r")

    print(f"\nDone. tagged={tagged} skipped={skipped} failed={failed}")
    n_tags = conn.execute("SELECT COUNT(*) FROM image_tags").fetchone()[0]
    print(f"Total tag rows in DB: {n_tags}")
    conn.close()


if __name__ == "__main__":
    run()
