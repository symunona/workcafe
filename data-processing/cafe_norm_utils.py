#!/usr/bin/env python3
"""
cafe_norm_utils.py — Shared utilities for cafe normalization pipeline.

- Haversine distance
- Ollama embedding (nomic-embed-text, 768-dim)
- Cosine similarity
- Levenshtein distance
- Name similarity heuristic (combined score)
- Korean/English name translation via qwen2.5:1.5b
- Chain detection heuristic
"""
import math
import json
import struct
import sqlite3
from typing import Optional
import httpx
import numpy as np

OLLAMA_BASE = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"
LLM_MODEL = "qwen2.5:1.5b"
EMBED_DIM = 768  # nomic-embed-text output dim


# ─── Distance ────────────────────────────────────────────────────────────────

def haversine_m(lat1, lon1, lat2, lon2) -> float:
    """Returns distance in meters between two lat/lon points."""
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


def lat_lon_bbox(lat, lon, radius_m):
    """Returns (min_lat, max_lat, min_lon, max_lon) for a radius in meters."""
    dlat = radius_m / 111000.0
    dlon = radius_m / (111000.0 * math.cos(math.radians(lat)))
    return lat - dlat, lat + dlat, lon - dlon, lon + dlon


# ─── Embeddings ──────────────────────────────────────────────────────────────

def get_embedding(text: str) -> Optional[np.ndarray]:
    """Embed text using nomic-embed-text via Ollama API. Returns float32 array."""
    try:
        resp = httpx.post(
            f"{OLLAMA_BASE}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text},
            timeout=30,
        )
        resp.raise_for_status()
        vec = resp.json()["embedding"]
        return np.array(vec, dtype=np.float32)
    except Exception as e:
        return None


def embed_to_blob(vec: np.ndarray) -> bytes:
    return vec.astype(np.float32).tobytes()


def blob_to_embed(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


# ─── String similarity ────────────────────────────────────────────────────────

def levenshtein(s1: str, s2: str) -> int:
    """Standard Levenshtein edit distance."""
    if s1 == s2:
        return 0
    s1, s2 = s1.lower().strip(), s2.lower().strip()
    m, n = len(s1), len(s2)
    if m == 0: return n
    if n == 0: return m
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, n + 1):
            temp = dp[j]
            if s1[i-1] == s2[j-1]:
                dp[j] = prev
            else:
                dp[j] = 1 + min(prev, dp[j], dp[j-1])
            prev = temp
    return dp[n]


def normalized_lev(s1: str, s2: str) -> float:
    """0.0 = identical, 1.0 = completely different."""
    s1, s2 = s1.lower().strip(), s2.lower().strip()
    max_len = max(len(s1), len(s2), 1)
    return levenshtein(s1, s2) / max_len


def name_similarity(n1: str, n2: str, emb1: Optional[np.ndarray] = None, emb2: Optional[np.ndarray] = None) -> dict:
    """
    Combined name similarity score.
    Returns dict with lev_score, cosine_score, combined (0..1, higher=more similar).
    """
    lev = 1.0 - normalized_lev(n1, n2)

    if emb1 is not None and emb2 is not None:
        cos = cosine_sim(emb1, emb2)
    else:
        cos = None

    if cos is not None:
        combined = 0.4 * lev + 0.6 * cos
    else:
        combined = lev

    return {"lev_score": lev, "cosine_score": cos, "combined": combined}


# ─── Branch suffix stripping ──────────────────────────────────────────────────

BRANCH_SUFFIXES = ["DT점", "DT", "역점", "공항점", "역사점", "터미널점", "점"]


def strip_branch(name: str) -> str:
    """Remove branch location suffixes from cafe name for canonical comparison."""
    result = name
    for suf in BRANCH_SUFFIXES:
        if result.endswith(suf) and len(result) > len(suf) + 1:
            result = result[:-len(suf)].strip()
    # Also handle space-separated last token like "강남 점"
    parts = result.split()
    if len(parts) > 1 and parts[-1] in ("점", "DT"):
        result = " ".join(parts[:-1])
    return result.strip()


# ─── LLM helpers (qwen2.5:1.5b) ──────────────────────────────────────────────

def llm_generate(prompt: str, max_tokens: int = 100, model: str = LLM_MODEL) -> str:
    try:
        resp = httpx.post(
            f"{OLLAMA_BASE}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": max_tokens, "temperature": 0.1},
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["response"].strip()
    except Exception as e:
        return ""


def get_english_name(korean_name: str) -> str:
    """Translate a Korean cafe name to English using qwen2.5:1.5b."""
    prompt = (
        f"Translate this Korean cafe/coffee shop name to English. "
        f"Return ONLY the English name, nothing else.\n"
        f"Korean name: {korean_name}\n"
        f"English name:"
    )
    result = llm_generate(prompt, max_tokens=30)
    # Clean up: take first line only
    return result.split("\n")[0].strip()


def is_chain_cafe(name: str, existing_chain_names: list[str]) -> dict:
    """
    Heuristic + LLM check: is this cafe a chain?
    Returns dict with is_chain (bool), chain_name (str or None), confidence (float).
    """
    name_lower = name.lower()

    # Well-known Korean chains (prefix/exact match) mapping to canonical English name
    CHAIN_MAPPING = {
        "스타벅스": "Starbucks",
        "starbucks": "Starbucks",
        "이디야": "Ediya Coffee",
        "이디야커피": "Ediya Coffee",
        "ediya": "Ediya Coffee",
        "빽다방": "Paik's Coffee",
        "메가커피": "Mega Coffee",
        "mega coffee": "Mega Coffee",
        "메가mgc": "Mega Coffee",
        "투썸플레이스": "A Twosome Place",
        "twosome": "A Twosome Place",
        "a twosome place": "A Twosome Place",
        "폴바셋": "Paul Bassett",
        "paul bassett": "Paul Bassett",
        "커피빈": "The Coffee Bean & Tea Leaf",
        "coffee bean": "The Coffee Bean & Tea Leaf",
        "할리스": "Hollys Coffee",
        "할리스커피": "Hollys Coffee",
        "hollys": "Hollys Coffee",
        "엔제리너스": "Angel-in-us",
        "angel-in-us": "Angel-in-us",
        "탐앤탐스": "Tom N Toms",
        "tom n toms": "Tom N Toms",
        "카페베네": "Caffe Bene",
        "caffe bene": "Caffe Bene",
        "드롭탑": "Cafe Droptop",
        "더벤티": "The Venti",
        "컴포즈커피": "Compose Coffee",
        "compose coffee": "Compose Coffee",
        "요거프레소": "Yoger Presso",
        "파스쿠찌": "Caffe Pascucci",
        "pascucci": "Caffe Pascucci",
    }
    for kr_en, canonical in CHAIN_MAPPING.items():
        if name_lower.startswith(kr_en) or kr_en in name_lower:
            return {"is_chain": True, "chain_name": canonical, "confidence": 0.95, "method": "known_list"}

    # Strip branch suffix then compare against known chain names
    base = strip_branch(name)
    for existing in existing_chain_names:
        base_existing = strip_branch(existing)
        score = max(
            1.0 - normalized_lev(base, existing),
            1.0 - normalized_lev(base, base_existing),
        )
        if score > 0.85:
            return {"is_chain": True, "chain_name": existing, "confidence": score, "method": "lev_match"}

    return {"is_chain": False, "chain_name": None, "confidence": 0.0, "method": "none"}


# ─── DB helpers ──────────────────────────────────────────────────────────────

def find_clean_cafes_nearby(conn: sqlite3.Connection, lat: float, lon: float, radius_m: float = 50) -> list:
    """Find clean_cafes within radius_m meters using bbox + haversine refinement."""
    min_lat, max_lat, min_lon, max_lon = lat_lon_bbox(lat, lon, radius_m)
    rows = conn.execute("""
        SELECT id, name, english_name, avg_lat, avg_lon, providers, source_ids, name_embedding, chain_id
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
                "name_embedding": blob_to_embed(row[7]) if row[7] else None,
                "chain_id": row[8],
                "distance_m": dist,
            })
    return result


def find_cafes_nearby_raw(conn: sqlite3.Connection, lat: float, lon: float,
                           radius_m: float = 50, exclude_provider: str = None) -> list:
    """Find raw scraped_cafes within radius_m (different provider only if exclude_provider set)."""
    min_lat, max_lat, min_lon, max_lon = lat_lon_bbox(lat, lon, radius_m)
    q = """
        SELECT id, name, provider, lat, lon, belongs_to_cafe_id, name_embedding
        FROM scraped_cafes
        WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?
    """
    params = [min_lat, max_lat, min_lon, max_lon]
    if exclude_provider:
        q += " AND provider != ?"
        params.append(exclude_provider)
    rows = conn.execute(q, params).fetchall()

    result = []
    for row in rows:
        dist = haversine_m(lat, lon, row[3], row[4])
        if dist <= radius_m:
            result.append({
                "id": row[0], "name": row[1], "provider": row[2],
                "lat": row[3], "lon": row[4],
                "belongs_to_cafe_id": row[5],
                "name_embedding": blob_to_embed(row[6]) if row[6] else None,
                "distance_m": dist,
            })
    return result
