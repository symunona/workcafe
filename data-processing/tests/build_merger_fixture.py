#!/usr/bin/env python3
"""
build_merger_fixture.py — Build a SYNTHETIC scraped DB for merger correctness tests.

Creates a tiny, fully-controlled scraped.db-shaped database containing
scraped_cafes + images rows for a set of designed merge test cases.
Never touches prod scraped.db / clean.db — writes only to the --out path
(default /tmp/merger_test_scraped.db).

Coordinates are placed precisely so distances between paired cafes are
known (~5 m hard-zone, ~15 m soft-zone, ~150 m standard-zone).

Provider counts are tuned so GOOGLE is the densest provider; the normalizer
processes densest-first, so google anchors are created before kakao rows —
which is exactly what exercises the order-independence fix (case 3).
"""
import os
import sqlite3
import argparse
import math

# meters-per-degree helpers (Seoul latitude ~37.5)
LAT0 = 37.50000
LON0 = 127.00000
M_PER_DEG_LAT = 110_574.0
M_PER_DEG_LON = 88_900.0   # ~ 111320*cos(37.5)


def off(lat_m=0.0, lon_m=0.0, base_lat=LAT0, base_lon=LON0):
    return (base_lat + lat_m / M_PER_DEG_LAT, base_lon + lon_m / M_PER_DEG_LON)


# Each case gets its own well-separated "block" (~2 km apart) so cases never
# spatially interfere with each other.
def block(i):
    return (LAT0 + i * 0.02, LON0 + i * 0.02)   # ~2.2 km steps


CASES = []  # list of dicts: id, provider, name, lat, lon, n_images, case, note


def add(cid, provider, name, lat, lon, n_images, case, note=""):
    CASES.append(dict(id=cid, provider=provider, name=name, lat=lat, lon=lon,
                      n_images=n_images, case=case, note=note))


def build_cases():
    CASES.clear()

    # ── Case 1: SAME provider, ~5 m apart → MUST NOT merge ────────────────────
    bl, bo = block(1)
    la, lo = off(0, 0, bl, bo)
    lb, lob = off(0, 5, bl, bo)            # 5 m east
    add("kakao_c1a", "kakao", "카페 모카하우스", la, lo, 2, 1, "same-provider A")
    add("kakao_c1b", "kakao", "카페 모카하우스점", lb, lob, 3, 1, "same-provider B (5m)")

    # ── Case 2: kakao + google, ~5 m apart, similar name → MUST merge ─────────
    bl, bo = block(2)
    la, lo = off(0, 0, bl, bo)
    lb, lob = off(0, 5, bl, bo)
    add("kakao_c2", "kakao", "테라로사 서울숲", la, lo, 2, 2, "cross-provider kakao")
    add("google_c2", "google", "테라로사 서울숲점", lb, lob, 4, 2, "cross-provider google (5m)")

    # ── Case 3: GOOGLE fed before KAKAO (densest-first) for same spot ─────────
    #   google is densest globally, so its anchor is created first; the kakao
    #   row must merge into it. Names similar, ~5 m apart.
    bl, bo = block(3)
    la, lo = off(0, 0, bl, bo)
    lb, lob = off(0, 5, bl, bo)
    add("google_c3", "google", "어니언 성수", la, lo, 3, 3, "order-indep google (anchor)")
    add("kakao_c3", "kakao", "어니언 성수점", lb, lob, 2, 3, "order-indep kakao (must merge in)")

    # ── Case 4a: cross-language, ~5 m apart (HARD zone) → MUST merge ──────────
    bl, bo = block(4)
    la, lo = off(0, 0, bl, bo)
    lb, lob = off(0, 5, bl, bo)
    add("kakao_c4a", "kakao", "앤트러사이트", la, lo, 2, "4a", "cross-lang KR, 5m")
    add("google_c4a", "google", "Anthracite Coffee", lb, lob, 3, "4a", "cross-lang EN, 5m (hard zone)")

    # ── Case 4b: cross-language, ~15 m apart, NO englishify → NOT merge ───────
    #   Pure-Levenshtein cross-script sim == 0.0, soft-zone needs >= 0.44.
    #   Documents the known limitation: without english_name, soft-zone
    #   cross-language cafes do NOT merge.
    bl, bo = block(5)
    la, lo = off(0, 0, bl, bo)
    lb, lob = off(0, 15, bl, bo)
    add("kakao_c4b", "kakao", "블루보틀 삼청", la, lo, 1, "4b", "cross-lang KR, 15m, no-eng")
    add("google_c4b", "google", "Blue Bottle Samcheong", lb, lob, 1, "4b", "cross-lang EN, 15m, no-eng")

    # ── Case 4c: cross-language, ~15 m apart, WITH englishify → MUST merge ────
    #   english_name lookup makes the cross-language pair match in soft zone.
    bl, bo = block(6)
    la, lo = off(0, 0, bl, bo)
    lb, lob = off(0, 15, bl, bo)
    add("kakao_c4c", "kakao", "프릳츠 도화", la, lo, 2, "4c", "cross-lang KR, 15m, eng-cache")
    add("google_c4c", "google", "Fritz Dohwa", lb, lob, 2, "4c", "cross-lang EN, 15m, eng-cache")

    # ── Case 5: genuinely different cafes ~150 m apart, different names ───────
    bl, bo = block(7)
    la, lo = off(0, 0, bl, bo)
    lb, lob = off(0, 150, bl, bo)
    add("kakao_c5", "kakao", "고양이정원카페", la, lo, 2, 5, "different A")
    add("google_c5", "google", "오로라브런치", lb, lob, 2, 5, "different B (150m)")

    # ── Padding google rows so GOOGLE is the densest provider ────────────────
    #   Count after the above: kakao=6, google=6. Add google padding far away
    #   (each isolated, unique name) to make google densest → processed first.
    bl, bo = block(20)
    for k in range(4):
        la, lo = off(0, k * 300, bl, bo)   # 300 m apart, no interference
        add(f"google_pad{k}", "google", f"패딩카페{k}유니크", la, lo, 0, "pad",
            "density padding (isolated)")

    return CASES


def build(out_path, copy_schema_from=None):
    if os.path.exists(out_path):
        os.remove(out_path)
    for ext in ("-wal", "-shm"):
        if os.path.exists(out_path + ext):
            os.remove(out_path + ext)

    conn = sqlite3.connect(out_path)
    conn.execute("PRAGMA journal_mode=WAL")

    # Schema mirrors prod scraped.db (read from prod is OK; we only copy DDL).
    conn.executescript("""
    CREATE TABLE scraped_cafes (
        id TEXT PRIMARY KEY,
        provider TEXT,
        provider_id TEXT,
        name TEXT,
        lat REAL,
        lon REAL,
        address TEXT,
        url TEXT,
        metadata TEXT,
        scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        belongs_to_cafe_id TEXT,
        name_embedding BLOB,
        llm_english TEXT,
        metadata_last_checked TIMESTAMP
    );
    CREATE TABLE images (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cafe_id TEXT NOT NULL,
        provider TEXT NOT NULL,
        local_path TEXT,
        image_url TEXT,
        gallery_url TEXT,
        photo_id TEXT,
        photo_type TEXT,
        tags TEXT,
        registered_at TEXT,
        width INTEGER,
        height INTEGER,
        file_size INTEGER,
        exif_date TEXT,
        exif_lat REAL,
        exif_lon REAL,
        scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        belongs_to_cafe_id TEXT
    );
    CREATE INDEX idx_images_cafe_id ON images(cafe_id);
    """)

    cases = build_cases()
    for c in cases:
        conn.execute(
            "INSERT INTO scraped_cafes (id, provider, provider_id, name, lat, lon, address, url, metadata) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (c["id"], c["provider"], c["id"], c["name"], c["lat"], c["lon"],
             f"addr {c['id']}", f"http://example/{c['id']}", "{}"),
        )
        for n in range(c["n_images"]):
            conn.execute(
                "INSERT INTO images (cafe_id, provider, local_path, image_url, photo_id, file_size) "
                "VALUES (?,?,?,?,?,?)",
                (c["id"], c["provider"], f"/images/{c['id']}/p{n}.jpg",
                 f"http://img/{c['id']}/{n}", f"{c['id']}_p{n}", 1234),
            )
    conn.commit()
    n_cafes = conn.execute("SELECT COUNT(*) FROM scraped_cafes").fetchone()[0]
    n_imgs = conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
    conn.close()
    print(f"Built fixture {out_path}: {n_cafes} scraped_cafes, {n_imgs} images")
    return out_path


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="/tmp/merger_test_scraped.db")
    args = p.parse_args()
    build(args.out)
