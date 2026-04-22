#!/usr/bin/env python3
"""
03_detect_chains.py — Detect cafe chains from scraped_cafes name frequency.

Clustering strategy (applied in order):
1. KNOWN_CHAINS dict  — hardcoded cross-language anchors for major chains
2. Brand-token containment — strip brand qualifiers (커피/Coffee/Express…),
                             if one brand token starts with the other → same chain
3. Levenshtein on brand token — covers spelling variants in same script
4. English cross-link — after resolving canonical_en, merge clusters whose
                        English names are similar (lev) or identical llm_english
5. LLM cross-language (--llm flag) — for Korean-only clusters without canonical_en,
                                     ask model if it matches any English cluster

Safe to re-run: clears and rebuilds cafe_chains.
"""
import os, sys, uuid, argparse

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..', 'scraper', 'lib'))

from cafe_norm_utils import normalized_lev, strip_branch, llm_generate
from db_client import DBClient

# ─── config ───────────────────────────────────────────────────────────────────
MIN_COUNT    = 5
LEV_THRESHOLD = 0.18   # brand-token lev ≤ this → same cluster
CROSS_LEV_EN  = 0.22   # English name lev for cross-linking

# Qualifiers stripped from suffix before brand-token comparison (same-script only)
BRAND_SUFFIX_QUALIFIERS = [
    "익스프레스", "Express", "EXPRESS",
    "커피", "Coffee", "COFFEE",
    "MGC",
]

# Known canonical (name_kr, name_en) keyed by lowercase trigger substring
KNOWN_CHAINS: dict[str, tuple[str, str]] = {
    "스타벅스":      ("스타벅스",    "Starbucks"),
    "starbucks":     ("스타벅스",    "Starbucks"),
    "이디야커피":    ("이디야커피",  "Ediya Coffee"),
    "이디야":        ("이디야커피",  "Ediya Coffee"),
    "ediya":         ("이디야커피",  "Ediya Coffee"),
    "빽다방":        ("빽다방",      "Paik's Coffee"),
    "paik":          ("빽다방",      "Paik's Coffee"),
    "메가mgc":       ("메가MGC커피", "Mega Coffee"),
    "메가커피":      ("메가MGC커피", "Mega Coffee"),
    "mega mgc":      ("메가MGC커피", "Mega Coffee"),
    "mega coffee":   ("메가MGC커피", "Mega Coffee"),
    "투썸플레이스":  ("투썸플레이스","A Twosome Place"),
    "twosome":       ("투썸플레이스","A Twosome Place"),
    "폴바셋":        ("폴바셋",      "Paul Bassett"),
    "폴 바셋":       ("폴바셋",      "Paul Bassett"),
    "paul bassett":  ("폴바셋",      "Paul Bassett"),
    "커피빈":        ("커피빈",      "The Coffee Bean & Tea Leaf"),
    "coffee bean":   ("커피빈",      "The Coffee Bean & Tea Leaf"),
    "할리스커피":    ("할리스커피",  "Hollys Coffee"),
    "할리스":        ("할리스커피",  "Hollys Coffee"),
    "hollys":        ("할리스커피",  "Hollys Coffee"),
    "엔제리너스":    ("엔제리너스",  "Angel-in-us Coffee"),
    "angel-in-us":   ("엔제리너스",  "Angel-in-us Coffee"),
    "angelinus":     ("엔제리너스",  "Angel-in-us Coffee"),
    "탐앤탐스":      ("탐앤탐스",    "Tom N Toms"),
    "tom n toms":    ("탐앤탐스",    "Tom N Toms"),
    "카페베네":      ("카페베네",    "Caffe Bene"),
    "caffe bene":    ("카페베네",    "Caffe Bene"),
    "드롭탑":        ("드롭탑",      "Cafe Droptop"),
    "더벤티":        ("더벤티",      "The Venti"),
    "the venti":     ("더벤티",      "The Venti"),
    "컴포즈커피":    ("컴포즈커피",  "Compose Coffee"),
    "compose coffee":("컴포즈커피",  "Compose Coffee"),
    "요거프레소":    ("요거프레소",  "Yoger Presso"),
    "파스쿠찌":      ("파스쿠찌",    "Caffe Pascucci"),
    "pascucci":      ("파스쿠찌",    "Caffe Pascucci"),
    "공차":          ("공차",        "Gong Cha"),
    "gong cha":      ("공차",        "Gong Cha"),
    "매머드":        ("매머드커피",  "Mammoth Coffee"),
    "mammoth":       ("매머드커피",  "Mammoth Coffee"),
    "팀홀튼":        ("팀홀튼",      "Tim Hortons"),
    "tim horton":    ("팀홀튼",      "Tim Hortons"),
    "바나프레소":    ("바나프레소",  "Bana Presso"),
    "설빙":          ("설빙",        "Sulbing"),
    "sulbing":       ("설빙",        "Sulbing"),
    "파리바게뜨":    ("파리바게뜨",  "Paris Baguette"),
    "paris baguette":("파리바게뜨",  "Paris Baguette"),
    "paris croissant":("파리바게뜨", "Paris Baguette"),
    "오설록":        ("오설록",      "Osulloc"),
    "osulloc":       ("오설록",      "Osulloc"),
}

SKIP_NAMES = {"Unknown", "Cafe", "Coffee", "카페", ""}


# ─── helpers ──────────────────────────────────────────────────────────────────

def is_korean(s: str) -> bool:
    return any('가' <= c <= '힣' for c in s)


def brand_token(name: str) -> str:
    """
    Strip branch suffixes THEN brand qualifiers to isolate the core brand word.
    Only strips same-script qualifiers from the suffix.
    e.g. '매머드익스프레스' → '매머드', 'Mammoth Coffee Express' → 'Mammoth'
    """
    result = strip_branch(name).strip()
    for q in BRAND_SUFFIX_QUALIFIERS:
        rl, ql = result.lower(), q.lower()
        if rl.endswith(ql) and len(result) > len(q) + 1:
            result = result[:-len(q)].strip()
    return result.lower().strip()


def same_script(a: str, b: str) -> bool:
    return is_korean(a) == is_korean(b)


def brand_containment(name_a: str, name_b: str) -> bool:
    """
    True if brand tokens are in a containment (prefix) relationship AND same script.
    '매머드커피' vs '매머드익스프레스' → brand tokens '매머드'/'매머드' → True
    'Tom N Toms' vs 'Tom N Toms Coffee' → 'tom n toms'/'tom n toms' → True
    """
    ta, tb = brand_token(name_a), brand_token(name_b)
    if not ta or not tb or len(ta) < 2 or len(tb) < 2:
        return False
    if not same_script(ta, tb):
        return False
    return ta.startswith(tb) or tb.startswith(ta)


def match_known(name: str) -> tuple[str, str] | None:
    nl = name.lower()
    for trigger, canonical in KNOWN_CHAINS.items():
        if trigger in nl:
            return canonical
    return None


def chain_uuid(canonical_kr: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"wc:chain:v1:{canonical_kr}"))


def llm_same_chain(name_kr: str, name_en: str) -> bool:
    prompt = (
        f"Are these two names the same cafe chain?\n"
        f"Korean name: '{name_kr}'\n"
        f"English name: '{name_en}'\n"
        f"Answer only: yes or no"
    )
    resp = llm_generate(prompt, max_tokens=5).strip().lower()
    return resp.startswith("yes") or resp == "y"


# ─── clustering ───────────────────────────────────────────────────────────────

def cluster(
    candidates: list[tuple[str, int, str | None]],
    use_llm: bool = False,
    verbose: bool = False,
) -> list[dict]:
    items = sorted(candidates, key=lambda x: -x[1])
    count_by_name = {n: c for n, c, _ in items}
    eng_by_name   = {n: e for n, _, e in items if e}

    known_clusters: dict[str, dict] = {}
    lev_clusters: list[dict] = []
    assigned: set[str] = set()

    for name, count, eng in items:
        if name in SKIP_NAMES or name in assigned:
            continue

        base     = strip_branch(name)
        bt       = brand_token(name)

        # 1. KNOWN_CHAINS
        known = match_known(name) or match_known(base)
        if known:
            kr, en = known
            if kr not in known_clusters:
                known_clusters[kr] = {"canonical_kr": kr, "canonical_en": en,
                                       "names": [], "count": 0}
            known_clusters[kr]["names"].append(name)
            known_clusters[kr]["count"] += count
            assigned.add(name)
            continue

        # 2. Brand-token containment against existing lev clusters
        merged = False
        for cl in lev_clusters:
            if brand_containment(name, cl["head"]):
                cl["names"].append(name)
                cl["count"] += count
                assigned.add(name)
                merged = True
                if verbose:
                    print(f"  [containment] '{name}' → '{cl['head']}'")
                break

        if merged:
            continue

        # 3. Levenshtein on brand token
        for cl in lev_clusters:
            if same_script(bt, cl["head_bt"]) and normalized_lev(bt, cl["head_bt"]) <= LEV_THRESHOLD:
                cl["names"].append(name)
                cl["count"] += count
                assigned.add(name)
                merged = True
                break

        if not merged:
            lev_clusters.append({
                "canonical_kr": None, "canonical_en": None,
                "names": [name], "count": count,
                "head": name, "head_base": base, "head_bt": bt,
            })
            assigned.add(name)

    # Resolve canonicals for lev clusters
    for cl in lev_clusters:
        kr_names = [n for n in cl["names"] if is_korean(n)]
        en_names = [n for n in cl["names"] if not is_korean(n)]
        cl["canonical_kr"] = (
            max(kr_names, key=lambda n: count_by_name.get(n, 0))
            if kr_names else cl["head"]
        )
        if en_names:
            cl["canonical_en"] = max(en_names, key=lambda n: count_by_name.get(n, 0))
        else:
            for n in cl["names"]:
                if eng_by_name.get(n):
                    cl["canonical_en"] = eng_by_name[n]
                    break

    # 4. Cross-link: lev cluster → known cluster via English name
    lev_absorbed: set[int] = set()
    for idx, cl in enumerate(lev_clusters):
        en = (cl.get("canonical_en") or "").lower()
        if not en:
            continue
        for known in known_clusters.values():
            kn_en = (known.get("canonical_en") or "").lower()
            if kn_en and normalized_lev(en, kn_en) <= CROSS_LEV_EN:
                known["names"] += cl["names"]
                known["count"] += cl["count"]
                lev_absorbed.add(idx)
                break

    # Cross-link: lev cluster → lev cluster via English name
    lev_merged: dict[int, int] = {}
    for i, cl_i in enumerate(lev_clusters):
        if i in lev_absorbed:
            continue
        en_i = (cl_i.get("canonical_en") or "").lower()
        if not en_i:
            continue
        for j, cl_j in enumerate(lev_clusters):
            if j >= i or j in lev_merged or j in lev_absorbed:
                continue
            en_j = (cl_j.get("canonical_en") or "").lower()
            if en_j and normalized_lev(en_i, en_j) <= CROSS_LEV_EN:
                cl_i["names"] += cl_j["names"]
                cl_i["count"] += cl_j["count"]
                lev_merged[j] = i
                if not is_korean(cl_i["canonical_kr"]) and is_korean(cl_j.get("canonical_kr", "")):
                    cl_i["canonical_kr"] = cl_j["canonical_kr"]

    surviving = [
        cl for idx, cl in enumerate(lev_clusters)
        if idx not in lev_absorbed and idx not in lev_merged
    ]

    all_clusters = list(known_clusters.values()) + surviving

    # 5. LLM cross-language: Korean-only clusters vs English clusters
    if use_llm:
        # Korean clusters still missing an English name
        kr_only = [cl for cl in all_clusters
                   if cl.get("canonical_en") is None and is_korean(cl["canonical_kr"])]
        # English clusters (canonical name is Latin-script)
        en_only = [cl for cl in all_clusters if not is_korean(cl["canonical_kr"])]
        absorbed: set[int] = set()

        def adjusted_len(s: str) -> int:
            # Korean syllable chars ≈ 2 Latin chars for length comparison
            return sum(2 if '가' <= c <= '힣' else 1 for c in s)

        for cl_kr in kr_only:
            for cl_en in en_only:
                if id(cl_en) in absorbed or id(cl_en) == id(cl_kr):
                    continue
                en_name = cl_en.get("canonical_en") or cl_en["canonical_kr"]
                kr_bt   = brand_token(cl_kr["canonical_kr"])
                en_bt   = brand_token(en_name)
                # Skip very short brand tokens (too ambiguous) and length outliers
                if adjusted_len(kr_bt) < 6 or adjusted_len(en_bt) < 4:
                    continue
                ratio = adjusted_len(kr_bt) / max(adjusted_len(en_bt), 1)
                if not (0.35 < ratio < 3.0):
                    continue
                print(f"  [LLM?] '{cl_kr['canonical_kr']}' ↔ '{en_name}'", end="", flush=True)
                if llm_same_chain(cl_kr["canonical_kr"], en_name):
                    print(" → YES")
                    cl_kr["canonical_en"] = en_name
                    cl_kr["names"] += cl_en["names"]
                    cl_kr["count"] += cl_en["count"]
                    absorbed.add(id(cl_en))
                else:
                    print(" → no")

        all_clusters = [cl for cl in all_clusters if id(cl) not in absorbed]

    return all_clusters


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-count", type=int, default=MIN_COUNT)
    parser.add_argument("--socket", default="/tmp/workcafe_db.sock")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--llm", action="store_true",
                        help="Use LLM to cross-link Korean↔English clusters")
    args = parser.parse_args()

    dbc = DBClient(socket_path=args.socket)

    print(f"Loading name frequencies (min_count={args.min_count})...")
    rows = dbc.fetchall("""
        SELECT name, COUNT(*) as cnt, MAX(llm_english) as eng
        FROM scraped_cafes
        GROUP BY name
        HAVING cnt >= ?
        ORDER BY cnt DESC
    """, (args.min_count,))
    candidates = [(r[0], r[1], r[2]) for r in rows]
    print(f"  Candidate names: {len(candidates)}")

    print("Clustering...")
    clusters = cluster(candidates, use_llm=args.llm, verbose=args.verbose)
    chains = [cl for cl in clusters if cl["count"] >= args.min_count]
    print(f"  Total clusters: {len(clusters)}  (≥{args.min_count}: {len(chains)})")

    if args.verbose:
        print()
        for cl in sorted(chains, key=lambda c: -c["count"]):
            aliases = [n for n in cl["names"]
                       if n not in (cl["canonical_kr"], cl["canonical_en"])]
            alias_str = f"  aliases={aliases}" if aliases else ""
            print(f"  [{cl['count']:>5}] {cl['canonical_kr']!r:30} / {cl['canonical_en']!r}{alias_str}")

    if args.dry_run:
        print("\n[dry-run] no writes.")
        return

    print("\nWriting to cafe_chains (clearing existing)...")
    dbc.execute("DELETE FROM cafe_chains")
    for cl in chains:
        cid = chain_uuid(cl["canonical_kr"])
        dbc.execute(
            "INSERT OR IGNORE INTO cafe_chains (id, name, name_english) VALUES (?, ?, ?)",
            (cid, cl["canonical_kr"], cl.get("canonical_en"))
        )
    total = dbc.fetchval("SELECT COUNT(*) FROM cafe_chains")
    print(f"  cafe_chains rows: {total}")


if __name__ == "__main__":
    main()
