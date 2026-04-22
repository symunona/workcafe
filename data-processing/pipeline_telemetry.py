#!/usr/bin/env python3
"""
pipeline_telemetry.py — Record merge-pipeline run stats to telemetry.log.

Usage:
    python3 pipeline_telemetry.py \
        --log telemetry.log \
        --start <unix_ts> \
        --steps "db-clean:12,dedup:3,reset:1,normalize:4894,images:8" \
        --db data/seoul/clean.db
"""
import os
import sys
import json
import time
import hashlib
import argparse
import sqlite3
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
PIPELINE_FILES = [
    os.path.join(_HERE, "db_clean.py"),
    os.path.join(_HERE, "00_dedup_raw_cafes.py"),
    os.path.join(_HERE, "04_normalize_pipeline.py"),
    os.path.join(_HERE, "06_update_image_links.py"),
    os.path.join(_HERE, "cafe_norm_utils.py"),
    os.path.join(_HERE, "pipeline_telemetry.py"),
    os.path.join(_HERE, "..", "..", "Justfile"),
]


def pipeline_version() -> str:
    """SHA256 of all pipeline scripts, truncated to 12 hex chars."""
    h = hashlib.sha256()
    for path in PIPELINE_FILES:
        h.update(path.encode())
        try:
            with open(path, "rb") as f:
                h.update(f.read())
        except FileNotFoundError:
            h.update(b"MISSING")
    return h.hexdigest()[:12]


def db_stats(db_path: str) -> dict:
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        def q(sql):
            try:
                return conn.execute(sql).fetchone()[0]
            except Exception:
                return "?"
        stats = {
            "scraped_cafes":    q("SELECT COUNT(*) FROM scraped_cafes"),
            "clean_cafes":      q("SELECT COUNT(*) FROM clean_cafes"),
            "multi_provider":   q("SELECT COUNT(*) FROM clean_cafes WHERE json_array_length(providers) > 1"),
            "chains":           q("SELECT COUNT(*) FROM cafe_chains"),
            "images_linked":    q("SELECT COUNT(*) FROM images WHERE belongs_to_cafe_id IS NOT NULL"),
            "unprocessed":      q("SELECT COUNT(*) FROM scraped_cafes WHERE belongs_to_cafe_id IS NULL"),
        }
        conn.close()
        return stats
    except Exception as e:
        return {"error": str(e)}


def fmt_dur(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s" if m else f"{s}s"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log",   default="telemetry.log")
    parser.add_argument("--start", type=float, required=True, help="Unix timestamp of pipeline start")
    parser.add_argument("--steps", default="", help="name:seconds,name:seconds,...")
    parser.add_argument("--db",    required=True, help="Path to clean.db")
    args = parser.parse_args()

    now      = time.time()
    total    = now - args.start
    version  = pipeline_version()
    started  = datetime.fromtimestamp(args.start, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    stats    = db_stats(args.db)

    steps = {}
    if args.steps:
        for part in args.steps.split(","):
            if ":" in part:
                name, secs = part.rsplit(":", 1)
                steps[name.strip()] = float(secs)

    lines = [
        "",
        "=" * 64,
        f"  merge-pipeline  v{version}",
        f"  started : {started}",
        f"  total   : {fmt_dur(total)}  ({total:.0f}s)",
        "=" * 64,
    ]

    if steps:
        lines.append("")
        lines.append("  Steps:")
        for name, secs in steps.items():
            pct = 100 * secs / total if total > 0 else 0
            lines.append(f"    {name:<22} {fmt_dur(secs):>8}  ({pct:.1f}%)")

    lines += [
        "",
        "  Results:",
        f"    {'Metric':<26} {'Value':>10}",
        f"    {'-'*26} {'-'*10}",
        f"    {'scraped_cafes':<26} {str(stats.get('scraped_cafes','?')):>10}",
        f"    {'clean_cafes':<26} {str(stats.get('clean_cafes','?')):>10}",
        f"    {'multi-provider':<26} {str(stats.get('multi_provider','?')):>10}",
        f"    {'chains':<26} {str(stats.get('chains','?')):>10}",
        f"    {'images_linked':<26} {str(stats.get('images_linked','?')):>10}",
        f"    {'unprocessed':<26} {str(stats.get('unprocessed','?')):>10}",
        "",
    ]

    block = "\n".join(lines)
    print(block)

    log_path = os.path.abspath(args.log)
    with open(log_path, "a") as f:
        f.write(block + "\n")
    print(f"  → appended to {log_path}")


if __name__ == "__main__":
    main()
