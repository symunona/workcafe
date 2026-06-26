# Scrape → Merge → Clean: real-time ingest mechanism

_2026-06-26 — how a freshly-scraped cafe flows from a provider into `clean.db`, gets deduped/merged, translated, and surfaced with images + tags._

This documents the **live** path (the `workcafe-pipeline` daemon), not the manual batch recipes. Manual recipes (`just merge-pipeline`, `just detect-chains`) run the same step scripts; the daemon just calls them on a loop.

---

## 0. The three databases

| DB | owner | holds | who writes |
|----|-------|-------|-----------|
| `scraped.db` | db_server (`/tmp/workcafe_db.sock`) | raw `scraped_cafes` + `images`, one row per **provider source** | place scrapers + image scrapers |
| `clean.db` | play db_server (`/tmp/workcafe_play_db.sock`) | a **copy** of scraped data + the deduped `clean_cafes`, `cafe_chains`, `image_tags`, `merge_log` | pipeline daemon + tagger |
| `englishify.db` | direct sqlite | `name_translations` cache: Korean name → English (persistent) | translate step |

Key idea: **`scraped.db` is the raw landing zone**; `clean.db` is the deduped product. A source row's primary key is `{provider}_{provider_id}` (e.g. `kakao_285841321`). One real-world cafe usually has **several** source rows (one per provider) that the merge collapses into **one** `clean_cafes` row.

---

## 1. System overview

```mermaid
flowchart TB
    subgraph scrapers["Scrapers (independent systemd units)"]
        gp["place: google / kakao / naver / osm<br/>spiral search"]
        gi["images: kakao_v3 / google_v1 / naver_v1<br/>download photos per source"]
        gm["metadata: kakao / naver"]
    end

    sdb[("scraped.db<br/>scraped_cafes + images")]
    gp -->|"INSERT source row"| sdb
    gi -->|"INSERT image rows<br/>(cafe_id = source id)"| sdb
    gm -->|"UPDATE metadata"| sdb

    subgraph daemon["workcafe-pipeline daemon (poll 30s)"]
        s1["1. sync (00)"]
        s2["2. translate (05)"]
        s3["3. merge (04 + 06)"]
        s1 --> s2 --> s3
    end

    sdb -->|"INSERT OR IGNORE<br/>new rows"| cdb
    cdb[("clean.db<br/>scraped_cafes (copy)<br/>clean_cafes / cafe_chains<br/>image_tags / merge_log")]
    daemon <-->|"play db socket"| cdb
    edb[("englishify.db")]
    s2 <--> edb

    subgraph tagger["GPU tagger (separate process)"]
        t1["tag_images_ram.py<br/>(swin_large) polls untagged images"]
    end
    cdb <--> t1

    api["Go API :8090"] --> cdb
    fe["React :5550"] --> api
```

The daemon, the tagger, the scrapers, and the API are **all separate processes**. They never call each other directly — they coordinate purely through DB row state (`status`, `belongs_to_cafe_id`, `tagged_at`). No queue table.

---

## 2. The daemon cycle (what runs in what order)

Every 30s the daemon runs three steps in a fixed order: **sync → translate → merge**. Translate runs *before* merge ("Order B") so a cafe already has an English name cached when it lands in `clean_cafes`.

```mermaid
flowchart TD
    start(["poll tick (30s)"]) --> sync

    subgraph S1["① sync  —  00_sync_from_scraped.py"]
        sync["INSERT OR IGNORE scraped.db → clean.db<br/>new rows: status='scraped', belongs_to_cafe_id=NULL"]
    end

    sync --> trans
    subgraph S2["② translate  —  reuse 05_englishify (qwen on CPU)"]
        trans["sync distinct names → englishify.db<br/>chain_prepass (exact + branch-aware)<br/>google_native_prepass<br/>ollama_batch translate NULLs (capped 30×10)"]
        promote["promote scraped → 'translated'<br/>(name now has english_name)"]
        trans --> promote
    end

    promote --> gate{"trigger merge?<br/>translated ≥ 30<br/>OR debounce 60s"}
    gate -- no --> idle["idle, wait next tick"]
    gate -- yes --> merge

    subgraph S3["③ merge  —  04_normalize + 06_link"]
        merge["04: process every scraped_cafes<br/>WHERE belongs_to_cafe_id IS NULL (ORDER BY id)"]
        link["06: images.belongs_to_cafe_id ← scraped_cafes.belongs_to_cafe_id"]
        flip["flip belongs_to≠NULL → status='merged'"]
        merge --> link --> flip
    end

    flip --> idle
```

**Status flow of a source row:** `scraped → translated → merged`. The status column lives in `clean.db.scraped_cafes` — it *is* the queue (no separate queue table).

Config (`data/pipeline.json`): `poll_interval_s=30`, `merge_debounce_s=60`, `translate_batch=30`, `chain_promote_min=5`, `image_priority_first_n=30`, `llm_on_cpu=true` (GPU reserved for the tagger).

---

## 3. What the merge actually looks at (step ③ internals)

For each unmerged source row, `04_normalize.process_cafe` searches existing `clean_cafes` **within 150 m** (bbox query) and decides: merge into one, or create a new clean cafe. Candidates already containing the **same provider** are skipped (a clean cafe holds at most one source per provider).

```mermaid
flowchart TD
    src["<b>source cafe S</b><br/>name · lat/lon · provider"]
    src --> near["candidates = clean_cafes within 150m<br/>nearest-first · skip any already holding S.provider"]

    near --> hard{"≤ 9m ?<br/><i>HARD</i>"}
    hard -- yes --> veto{"different_business?<br/>brand veto"}
    veto -- no --> M1(["MERGE · distance"])
    veto -- "yes" --> soft

    hard -- no --> soft{"≤ 20m AND<br/>name_sim ≥ 0.44 ?<br/><i>SOFT</i>"}
    soft -- yes --> M2(["MERGE · name"])
    soft -- no --> std{"≤ 150m AND<br/>name_sim ≥ 0.80 ?<br/><i>STANDARD</i>"}
    std -- yes --> M2
    std -- no --> chain{"candidate is chain AND<br/>S same chain ?"}
    chain -- yes --> M3(["MERGE · chain"])
    chain -- no --> llm{"LLM: top-5 nearest<br/>same cafe? (qwen)"}
    llm -- yes --> M4(["MERGE · llm"])
    llm -- no --> NEW(["CREATE new clean_cafe"])

    M1 --> DONE["set belongs_to_cafe_id<br/>+ merge_log row"]
    M2 --> DONE
    M3 --> DONE
    M4 --> DONE

    classDef merge fill:#d3f9d8,stroke:#2b8a3e,color:#000;
    classDef create fill:#d0ebff,stroke:#1971c2,color:#000;
    classDef veton fill:#ffe3e3,stroke:#c92a2a,color:#000;
    class M1,M2,M3,M4,DONE merge;
    class NEW create;
    class veto veton;
```

Read it as a **priority ladder**: evaluate candidates nearest-first; the first rule that fires wins. A `different_business` veto disqualifies the cheap 9 m distance-merge, so evaluation falls through to the name/chain/LLM rules — which a true different-business pair (low `name_sim`, no shared chain) fails, landing on **CREATE new**.

- **`name_sim`** = best of three: raw Levenshtein+cosine, branch-stripped (`스타벅스 명동점` → `스타벅스`), and English-vs-English (via englishify cache).
- **`different_business`** (the 2026-06-26 fix): inside the 9 m hard zone, veto the blind distance-merge when brand identities clearly differ (one is a known chain and the other isn't, or two different chains). Stops co-located businesses in one building from swapping records.
- **On MERGE** → `merge_into_clean_cafe`: append `source_id`, add provider, recompute `avg_lat/avg_lon` as a running mean, backfill address/url/metadata if empty, write a `merge_log` row (`ts, method, detail`).
- **On CREATE** → new `clean_cafes` row, id = `uuid5(source_id)` (deterministic/stable). Chain assigned via known-list or on-the-fly promotion (a brand token seen ≥5 times becomes a chain). English name from the chain (authoritative) or the englishify cache.
- Either way → `scraped_cafes.belongs_to_cafe_id = clean_id`. Finally `propagate_english_names` refreshes `clean_cafes.english_name` (chain canonical name wins over a per-name translation).

---

## 4. Concrete walk-through

Building at `해운대해변로 277`. Two real businesses stacked in it: **Hollys Coffee** (`할리스`) and a brunch cafe **Working Holiday** (`워킹홀리데이`). Watch them flow in.

### Stage A — first source ever (Google finds Working Holiday)

```mermaid
sequenceDiagram
    participant GS as Google place scraper
    participant SDB as scraped.db
    participant D as pipeline daemon
    participant EDB as englishify.db
    participant CDB as clean.db

    GS->>SDB: INSERT google_…41040081<br/>"워킹홀리데이 해운대 …brunch"
    Note over D: next 30s tick
    D->>CDB: ① sync → status='scraped', belongs_to=NULL
    D->>EDB: ② translate "워킹홀리데이 해운대"<br/>→ "Working Holiday Harborview"
    D->>CDB: promote → status='translated'
    Note over D: ③ merge (debounce/threshold)
    D->>CDB: 150m search → NOTHING nearby
    D->>CDB: CREATE clean_cafe 155f8e77<br/>name=워킹홀리데이, providers=[google]
    D->>CDB: source.belongs_to = 155f8e77, status='merged'
```

Result: one clean cafe, one provider.

```mermaid
flowchart LR
    g1["google_…41040081<br/>워킹홀리데이 …brunch"] --> C155["clean_cafe 155f8e77<br/>워킹홀리데이 해운대<br/>providers: [google]"]
```

### Stage B — second source, SAME cafe (Kakao + Naver find Working Holiday)

```mermaid
sequenceDiagram
    participant KS as Kakao/Naver scrapers
    participant SDB as scraped.db
    participant D as pipeline daemon
    participant CDB as clean.db

    KS->>SDB: INSERT kakao_285841321 "워킹홀리데이 해운대"
    KS->>SDB: INSERT naver_2049577038 "워킹홀리데이 해운대"
    D->>CDB: ① sync → 2 new rows status='scraped'
    D->>CDB: ② translate (name already cached) → 'translated'
    Note over D: ③ merge — kakao row first
    D->>CDB: 150m search → finds 155f8e77 (~3m away)
    D->>CDB: HARD zone, names match → MERGE (method=distance)<br/>providers=[google,kakao], avg recomputed
    Note over D: naver row next
    D->>CDB: finds 155f8e77, provider naver not present → MERGE
    D->>CDB: providers=[google,kakao,naver]
```

Result: still **one** clean cafe, now three sources collapsed into it.

```mermaid
flowchart LR
    k1["kakao_285841321"] --> C155
    n1["naver_2049577038"] --> C155
    g1["google_…41040081"] --> C155
    C155["clean_cafe 155f8e77<br/>워킹홀리데이 해운대<br/>providers: [google, kakao, naver]<br/>avg of 3 coords"]
```

### Stage C — different business, SAME building (Hollys arrives <9 m away)

This is where the `different_business` veto earns its keep. Without it, the Hollys source would blind-merge into the Working Holiday cluster by raw distance.

```mermaid
sequenceDiagram
    participant KS as Kakao scraper
    participant SDB as scraped.db
    participant D as pipeline daemon
    participant CDB as clean.db

    KS->>SDB: INSERT kakao_27365973 "할리스 부산해운대점"
    D->>CDB: ① sync → status='scraped'
    D->>CDB: ② translate / chain_prepass → "Hollys Coffee" (chain), 'translated'
    Note over D: ③ merge
    D->>CDB: 150m search → 155f8e77 ~3-12m away
    D->>CDB: HARD zone BUT different_business?<br/>Hollys=known chain, 155f8e77=not → VETO
    D->>CDB: skip candidate → no other match
    D->>CDB: CREATE clean_cafe 886f89cd<br/>name=할리스, chain=Hollys, providers=[kakao]
```

Result: **two** clean cafes at the same coordinates — correct, they are two businesses.

```mermaid
flowchart TB
    subgraph bldg["해운대해변로 277 (one building)"]
        C155["clean_cafe 155f8e77<br/>워킹홀리데이 해운대 (brunch)<br/>google + kakao + naver"]
        C886["clean_cafe 886f89cd<br/>할리스 부산해운대점 (Hollys)<br/>chain=Hollys, kakao + google"]
    end
```

> Before the fix, the 9 m blind merge attached each building-mate's Google record to the *wrong* sibling (a swap). The veto prevents new swaps; `scripts/fix_swapped_sources.py` repaired the rows already committed.

---

## 5. Images → tags (how labeling gets fed)

There is **no explicit labeling queue**. Image scrapers download + downscale photos to disk and insert an image **row** into `scraped.db`; sync copies that **row** (not the file) into `clean.db`; `06` links it to the clean cafe; the GPU tagger polls for anything untagged.

Two distinct things move — keep them apart:

- **Bytes (the `.jpg` file) → disk, once.** The scraper saves to `DATA_DIR/{provider}/{safe_id}/images/photo_NNNN.jpg` via `save_image()`, which **downscales at scrape time**: re-encode JPEG **quality 75**, resize longest side to **≤1024 px** (LANCZOS, only if larger). The DB stores `local_path` as a **web path** (`/images/kakao/…`), not the file's bytes. Files are written once and never copied again.
- **Row (the image record) → DBs.** `cafe_id` = the **source** id (e.g. `kakao_285841321`), plus `local_path`, dimensions, exif. The row is what `00` copies `scraped.db.images → clean.db.images` (INSERT OR IGNORE); both DBs' rows reference the same on-disk file.

```mermaid
flowchart TD
    is["image scrapers<br/>kakao_v3 / google_v1 / naver_v1"] -->|"downscale (JPEG q75, ≤1024px)<br/>write .jpg"| disk[("disk<br/>DATA_DIR/{provider}/{id}/images/")]
    is -->|"INSERT image ROW<br/>cafe_id = SOURCE id, local_path"| simg[("scraped.db.images")]
    simg -->|"① sync (00): copies ROWS only<br/>(files stay on disk)"| cimg[("clean.db.images")]
    cimg -->|"③ link (06)<br/>belongs_to_cafe_id ←<br/>scraped_cafes.belongs_to_cafe_id"| linked["image rows now point at<br/>their clean_cafe"]

    linked --> tagger
    subgraph tagger["GPU tagger — tag_images_ram.py (swin_large)"]
        pick["poll images WHERE file_size>0 AND local_path set<br/>ORDER: never-tagged first, then old-tagger<br/>(optional: cafes with most images first)"]
        tag["RAM++/swin → image_tags rows"]
        roll["aggregate per clean_cafe →<br/>UPDATE clean_cafes.tags"]
        pick --> tag --> roll
    end
```

Notes:
- An image's `belongs_to_cafe_id` is **inherited from its source's** `belongs_to_cafe_id`. So when the merge (or the swap-fix) moves a source between clean cafes, `06` re-points that source's images on the next cycle — tags follow the source automatically.
- Image-scrape **order** is steered per provider: kakao serves `priority DESC` first (set by `mark_region_image_priority.py` for a region) and caps ~`image_priority_first_n=30` photos/cafe breadth-first; naver is `RANDOM()`. This front-loads ~30 photos/cafe across a region before deep backlog.
- The tagger runs on `clean.db` as its own process (`just image-pipeline`), on the GPU, while the pipeline LLM stays on CPU — so tagging and merging never contend for the GPU.

---

## 6. One-glance summary

```mermaid
flowchart LR
    A["provider scraper"] -->|source row| B[("scraped.db")]
    B -->|sync 00| C[("clean.db<br/>status=scraped")]
    C -->|translate 05| D["status=translated<br/>englishify.db filled"]
    D -->|merge 04| E{"within 150m<br/>match?"}
    E -->|yes| F["merge into clean_cafe<br/>+merge_log"]
    E -->|no| G["new clean_cafe"]
    F --> H["status=merged<br/>belongs_to set"]
    G --> H
    H -->|link 06| I["images repointed"]
    I --> J["GPU tagger → image_tags<br/>→ clean_cafes.tags"]
    H --> K["Go API → React map"]
```

**Order, for one Google-first cafe:** scrape→`scraped.db` ➜ sync→`clean.db (scraped)` ➜ **englishify** (translate, before merge) ➜ **merge** (radius + name + chain + brand-veto, else new) ➜ link images ➜ status `merged` ➜ tagger labels images → rolls tags onto the clean cafe ➜ API serves it.
