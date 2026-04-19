#!/usr/bin/env python3
"""
03_experiment.py — Validate normalization algorithms on real data before full pipeline.

Tests:
1. Distance: find cross-provider duplicates (starbucks, ediya)
2. Name similarity: known same/different cafes
3. Embedding: verify model is running
4. Chain detection: starbucks, ediya, indie cafes
5. English name: translate a few Korean names
"""
import os
import sys
import sqlite3

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..'))

from normalize.cafe_norm_utils import (
    haversine_m, name_similarity, get_embedding, get_english_name,
    is_chain_cafe, find_cafes_nearby_raw, cosine_sim, blob_to_embed
)

DB_PATH = os.path.abspath(os.path.join(_HERE, '..', '..', 'data', 'seoul', 'cafedata.db'))

conn = sqlite3.connect(DB_PATH, timeout=30)
conn.row_factory = sqlite3.Row


print("=" * 60)
print("TEST 1: Distance — find cross-provider matches within 50m")
print("=" * 60)

rows = conn.execute("""
    SELECT c1.id, c1.name, c1.provider, c1.lat, c1.lon,
           c2.id, c2.name, c2.provider, c2.lat, c2.lon
    FROM cafes c1 JOIN cafes c2 ON c1.id < c2.id
    WHERE c1.provider != c2.provider
      AND ABS(c1.lat - c2.lat) < 0.0005
      AND ABS(c1.lon - c2.lon) < 0.0007
    LIMIT 200
""").fetchall()

matches = []
for r in rows:
    dist = haversine_m(r[3], r[4], r[8], r[9])
    if dist <= 50:
        sim = name_similarity(r[1], r[6])
        matches.append((dist, r[1], r[2], r[6], r[5], sim['combined']))

matches.sort()
print(f"Found {len(matches)} cross-provider pairs within 50m (from 200 bbox candidates)")
print("\nTop 15 closest pairs:")
for dist, n1, p1, n2, p2, sim in matches[:15]:
    print(f"  {dist:5.1f}m  [{p1}] {n1!r:35s}  [{p2}] {n2!r:35s}  sim={sim:.2f}")


print("\n" + "=" * 60)
print("TEST 2: Name similarity — known same cafe, different providers")
print("=" * 60)

pairs_same = [
    ("스타벅스 명동점", "Starbucks Myeongdong"),
    ("이디야커피 을지로3가점", "이디야커피 을지로3가점"),
    ("Hell Cafe Music", "헬카페뮤직"),
]
pairs_diff = [
    ("스타벅스 명동점", "이디야커피 을지로점"),
    ("블루보틀 커피", "카페 에이바웃"),
]

print("\nSame cafe pairs (expect high score):")
for n1, n2 in pairs_same:
    r = name_similarity(n1, n2)
    print(f"  {n1!r} vs {n2!r}")
    print(f"    lev={r['lev_score']:.2f}  combined={r['combined']:.2f}")

print("\nDifferent cafe pairs (expect low score):")
for n1, n2 in pairs_diff:
    r = name_similarity(n1, n2)
    print(f"  {n1!r} vs {n2!r}")
    print(f"    lev={r['lev_score']:.2f}  combined={r['combined']:.2f}")


print("\n" + "=" * 60)
print("TEST 3: Embedding check")
print("=" * 60)

test_names = ["스타벅스 명동점", "Starbucks Myeongdong", "이디야커피", "블루보틀 커피"]
embeddings = {}
for name in test_names:
    emb = get_embedding(name)
    if emb is not None:
        embeddings[name] = emb
        print(f"  OK [{len(emb)}-dim]: {name!r}")
    else:
        print(f"  FAIL: {name!r} — is ollama running? (run: ollama serve)")

if len(embeddings) >= 2:
    names = list(embeddings.keys())
    print("\nCosine similarities with embeddings:")
    for i, n1 in enumerate(names):
        for n2 in names[i+1:]:
            sim = cosine_sim(embeddings[n1], embeddings[n2])
            print(f"  {n1!r} vs {n2!r}: {sim:.3f}")

    print("\nCombined similarity (lev+cos):")
    for n1, n2 in [
        ("스타벅스 명동점", "Starbucks Myeongdong"),
        ("스타벅스 명동점", "이디야커피"),
        ("스타벅스 명동점", "스타벅스 강남점"),
    ]:
        if n1 in embeddings and n2 in embeddings:
            r = name_similarity(n1, n2, embeddings[n1], embeddings[n2])
            print(f"  {n1!r} vs {n2!r}: combined={r['combined']:.3f}  (lev={r['lev_score']:.2f}, cos={r['cosine_score']:.2f})")


print("\n" + "=" * 60)
print("TEST 4: Chain detection")
print("=" * 60)

chain_tests = [
    ("스타벅스 명동점", True),
    ("이디야커피 을지로3가점", True),
    ("메가커피 혜화점", True),
    ("도시바 서순라길점", False),
    ("갤러리 소이엔 카페", False),
    ("Hell Cafe Music", False),
    ("블루보틀 커피", False),
]

for name, expected_chain in chain_tests:
    result = is_chain_cafe(name, [])
    icon = "✓" if result['is_chain'] == expected_chain else "✗"
    print(f"  {icon} {name!r}: is_chain={result['is_chain']} (expected={expected_chain}) conf={result['confidence']:.2f} method={result['method']}")


print("\n" + "=" * 60)
print("TEST 5: English name generation")
print("=" * 60)

korean_names = [
    "스타벅스 명동점",
    "이디야커피 을지로3가점",
    "블루보틀 커피",
    "갤러리 소이엔 카페",
    "카페 에이바웃",
]

for name in korean_names:
    english = get_english_name(name)
    print(f"  {name!r} → {english!r}")


print("\n" + "=" * 60)
print("TEST 6: Starbucks cluster — same chain, different providers")
print("=" * 60)

sbux = conn.execute("""
    SELECT id, name, provider, lat, lon FROM cafes
    WHERE name LIKE '%스타벅스%' OR name LIKE '%Starbucks%'
    ORDER BY lat, lon
    LIMIT 30
""").fetchall()

print(f"Total Starbucks entries: {len(sbux)}")
clustered = {}
for row in sbux:
    placed = False
    for key in list(clustered.keys()):
        rep = clustered[key][0]
        if haversine_m(rep[3], rep[4], row[3], row[4]) < 100:
            clustered[key].append(row)
            placed = True
            break
    if not placed:
        clustered[id(row)] = [row]

multi = {k: v for k, v in clustered.items() if len(v) > 1}
print(f"Clusters with >1 provider entry: {len(multi)}")
for cluster in list(multi.values())[:5]:
    print(f"  Cluster ({len(cluster)} entries):")
    for r in cluster:
        print(f"    [{r[2]:8s}] {r[1]!r:45s} ({r[3]:.4f}, {r[4]:.4f})")


conn.close()
print("\nDone.")
