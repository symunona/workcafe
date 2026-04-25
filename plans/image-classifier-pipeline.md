# Image Classifier Pipeline

## Model
CLIP ViT-B/32 (`openai/clip-vit-base-patch32`) via HuggingFace transformers.
Runs on CUDA (GTX 1050 Ti, 4GB VRAM). torch 2.6.0+cu124.

## Tags
```
chairs, large chairs, tables, large tables, power plugs,
interior of a cafe, exterior of a building,
food, drinks, windows, laptops
```

## DB target
`data/seoul/clean.db` — `image_tags` table added by tagger (idempotent).
Also auto-created on API startup (`CREATE TABLE IF NOT EXISTS`).

Schema:
```sql
CREATE TABLE image_tags (
    image_id INTEGER NOT NULL REFERENCES images(id),
    tag      TEXT    NOT NULL,
    score    REAL    NOT NULL DEFAULT 1.0,
    PRIMARY KEY (image_id, tag)
);
```

## Tagger script
```
python scripts/tag_images_clip.py
```
Samples top 100 cafes by image count. Threshold: score >= 0.20. Skips already-tagged images.

## History snapshot
`data/seoul/history/clean_image_tags_2026-04-23.db` — baseline before tagging.

## Pipeline notes
- `just merge-pipeline` replaces clean.db (wipes image_tags). Re-run tagger after.
- Next step: subtype classifiers (high/low chair, etc.) once labeled data exists.
- Next step: filter cafes in API by tag (`?tag=chairs`).
