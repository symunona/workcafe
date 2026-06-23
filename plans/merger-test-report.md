# Cafe Merger — Post-Merge DB Correctness Test Report

Date: 2026-06-23
Scope: `data-processing/04_normalize_pipeline.py` (the merger) + `06_update_image_links.py`
(image linking), focused on post-merge DB correctness, especially images and the
`images.belongs_to_cafe_id` link table.

Deliverables:
- Fixture builder: `data-processing/tests/build_merger_fixture.py`
- Test runner (re-runnable, idempotent, self-cleaning): `data-processing/tests/run_merger_tests.py`
- This report.

Run it:
```
./venv/bin/python3 data-processing/tests/run_merger_tests.py        # cleans up after
./venv/bin/python3 data-processing/tests/run_merger_tests.py --keep # leaves /tmp DBs for inspection
```

## Data safety

- Prod `data/seoul/scraped.db` and `clean.db` are NEVER mutated. They are read once
  (schema only) to mirror DDL. All work happens in `/tmp/merger_test_*.db`.
- Dedicated db_server socket+pidfile `/tmp/merger_test.sock` / `/tmp/merger_test.pid`
  (`--unsafe-any-db`, since the DB isn't named `*scraped*`). Killed and removed on exit,
  including on failure (`finally`). No collision with prod (`/tmp/workcafe_db.sock`) or
  play (`/tmp/workcafe_play_db.sock`).

## How the merger works (verified by reading the code)

- Default runs (this test, `test-merge-naebang`, `merge-pipeline`) do NOT pass `--embed`,
  so `name_similarity` is **pure Levenshtein** (`combined = lev_score`); no embeddings,
  deterministic, no `nomic-embed-text` needed.
- Merge zones (per `04_normalize_pipeline.py`):
  - HARD `<= 9 m`: unconditional merge (GPS noise floor), regardless of name/language.
  - SOFT `9–20 m`: merge if best name-sim `>= 0.44`.
  - STANDARD `20–150 m`: merge if best name-sim `>= 0.8`.
  - Chain-canonical match at any distance; LLM fallback last.
- **Same-provider guard** (line 259): `if cafe["provider"] in providers: continue` — two
  cafes of the same provider can never collapse into one clean_cafe.
- Providers processed **densest-first**; every provider does a spatial merge (the recent
  fix — kakao is no longer insert-only, so order no longer matters).
- LLM fallback (`ask_llm_to_merge`) returns `None` when ollama lacks the model — so it is a
  no-op here. All merges below are achieved WITHOUT the LLM, by geometry / Levenshtein /
  english_name, which is the robust path.

## Test design — cases and expected outcomes (written BEFORE running)

Fixture: 18 synthetic `scraped_cafes` in 8 well-separated ~2 km blocks (cases never
interfere), plus 4 isolated google "padding" rows so **google is the densest provider**
(forces google-first processing → exercises the order-independence fix). 31 images total
(N per cafe, controlled).

Name-similarity values were precomputed to design exact expectations:
- `테라로사 서울숲` vs `테라로사 서울숲점` = 0.86 (same-script, high)
- `어니언 성수` vs `어니언 성수점` = ~0.86
- `앤트러사이트` vs `Anthracite Coffee` = 0.0 (cross-script Levenshtein is 0)
- `블루보틀 삼청` vs `Blue Bottle Samcheong` = 0.05 (cross-script)
- `테라로사` vs `Terarosa` = 0.0

| # | Setup | Distance | Expected |
|---|-------|----------|----------|
| 1 | 2× kakao, similar Korean names | ~5 m | NO merge (same-provider guard) → 2 clean_cafes |
| 2 | kakao + google, similar Korean | ~5 m | MERGE (hard zone) → 1 clean, providers `{kakao,google}`, both source_ids |
| 3 | google (anchor) fed before kakao, similar Korean | ~5 m | MERGE → 1 clean (order independence: kakao merges into google anchor) |
| 4a | kakao Korean + google Latin | ~5 m | MERGE via hard zone (geometry beats language) |
| 4b | kakao Korean + google Latin, NO englishify | ~15 m | NO merge — cross-script lev=0 < 0.44 soft threshold (documents a real limitation) |
| 4c | kakao Korean + google Latin, WITH englishify cache (matching english_name) | ~15 m | MERGE via english_name comparison in soft zone |
| 5 | kakao + google, different names | ~150 m | NO merge → 2 clean_cafes |
| 6 | every cafe has N images | — | post-`06_link`: every image's `belongs_to_cafe_id` == its parent's clean id; merged sources share one clean id; count preserved; no orphans, no dangling links |
| 7 | run merge + link a 2nd time | — | clean_cafes count stable, link count stable, no duplicate source_ids |

## Actual results

Synthetic suite: **17/17 assertions PASS.** 18 scraped → 14 clean_cafes (4 merges: cases
2, 3, 4a, 4c). 31 images, all correctly linked, count preserved, idempotent.

| Case | Result | Evidence |
|------|--------|----------|
| 1 same-provider guard | PASS | two distinct clean ids for the two kakao rows |
| 2 cross-provider merge | PASS | 1 clean, `providers=['google','kakao']`, `source_ids=['google_c2','kakao_c2']` |
| 3 order independence (google-first) | PASS | 1 clean, kakao merged into google anchor, `providers=['google','kakao']` |
| 4a cross-language hard zone | PASS | kakao & google share one clean id |
| 4b cross-language soft zone, no-eng | PASS (expected NON-merge) | two distinct clean ids — see Limitation below |
| 4c cross-language soft zone, eng-cache | PASS | merged via `english_name` lookup |
| 5 different cafes 150 m | PASS | two distinct clean ids |
| 6.0 image count preserved | PASS | 31 == 31 |
| 6.1 every image → parent's clean id | PASS | 0 mismatched/orphan |
| 6.2 no dangling image links | PASS | 0 links to non-existent clean_cafe |
| 6.3–6.5 merged cases: images on one clean id | PASS | each merged pair's images all share the single clean id |
| 6.6 no orphan images under linked parent | PASS | 0 |
| 7.0 clean_cafes stable on rerun | PASS | 14 == 14 |
| 7.1 linked images stable on rerun | PASS | 31 == 31 |
| 7.2 no duplicate source_ids | PASS | 0 |

### Image-linking known-bug verification (KNOWN BUG TO VERIFY)

The image scraper writes `images.belongs_to_cafe_id` from `scraped.db`, which the merger
never touches (it writes `clean.db`), so images are unlinked until `06_link` runs. In the
fixture all 31 images start with `belongs_to_cafe_id IS NULL`. After normalize + `06_link`:
all 31 are linked, every link equals the parent `scraped_cafes.belongs_to_cafe_id`, and
merged sources collapse onto one clean id. **`06_update_image_links.py` correctly links
images post-merge — confirmed.** (Note: the normalizer itself already writes image links
inline at lines 514–517, so `06_link` is partly redundant for a single-DB run; it remains
the authoritative pass for the real two-DB scraped→clean flow, which this test proves works.)

## Regression: `just test-merge-naebang`

Result: **PASS, 6/6** after a fix (see Bug 2). 144 real scraped cafes → 93 clean, 51
merges; the 6 reference same-place groups all merged correctly across providers and
languages (Starbucks/스타벅스, Compose Coffee/컴포즈커피, Mega Coffee/메가MGC커피, etc.).

## Bugs found

### Bug 1 — `01_migrate_db.py` does not create the `clean_cafes.metadata` column (MEDIUM, latent)

`01_migrate_db.py`'s `CREATE TABLE clean_cafes` does NOT include `metadata` (nor
`has_custom_website` / `custom_website_url`; it does backfill `tags`). But
`04_normalize_pipeline.py`'s `create_clean_cafe` INSERTs into `metadata`. So when
`clean_cafes` is created **purely** by `01_migrate_db.py` from scratch, every merge errors:

```
DB execute error: table clean_cafes has no column named metadata
```

Verified directly: run `01_migrate_db.py` on a brand-new DB → resulting `clean_cafes`
columns are `... name_embedding, tags, created_at, updated_at` — **no `metadata`**. In this
test, the INSERT then failed for all 18 rows (0 clean_cafes) until the harness reconciled
the schema.

Why it is currently **latent** (doesn't bite prod or `test-merge-naebang`): prod
`data/seoul/scraped.db` already carries a leftover `clean_cafes` table **with** `metadata`
(from past full-rebuild runs). `create_subset.py` copies that table into the naebang test
DB, and migrate's `CREATE TABLE IF NOT EXISTS` leaves it untouched — so `metadata` is
present and naebang passes 6/6. Likewise the live `clean.db` already has the column. The bug
only manifests on a genuinely clean slate where no prior `clean_cafes` exists (e.g. a fresh
deployment, or any path that builds clean_cafes solely via `01_migrate_db.py`). The
synthetic fixture builds exactly that clean slate, so it surfaces the gap deterministically.

Fix options (not applied — flagged for the owner):
- Add `metadata TEXT` (and `has_custom_website INTEGER DEFAULT 0`, `custom_website_url TEXT`)
  to `01_migrate_db.py` — either in the `CREATE TABLE` or as `IF NOT EXISTS`-style backfill
  ALTERs alongside the existing `tags` ALTER, OR
- have `create_clean_cafe` tolerate a missing `metadata` column.

The test harness works around this with `reconcile_clean_schema()` (adds the columns the
live prod `clean.db` has) so the merger logic itself can be exercised against the real
production schema.

### Bug 2 — `test-merge-naebang` recipe could not start its db_server (FIXED)

The recipe launched `db_server.py --db /tmp/naebang_clean.db ... --replace` with no
`--unsafe-any-db`. `db_server.py` has a safety check (added/tightened later) that refuses
any DB whose filename does not contain `scraped`. So the recipe aborted at **Step 2/4**:

```
ERROR: test db_server did not start
SAFETY CHECK FAILED: DB path '/tmp/naebang_clean.db' does not look like scraped.db ...
```

Fix applied (one line in `Justfile`): added `--unsafe-any-db` to the recipe's `db_server.py`
invocation. Re-ran → **6/6 PASS**. (This recipe is intentionally pointed at a non-scraped
test DB, so the override is appropriate.)

## Conclusion

The merger's post-merge DB correctness is **sound** for all designed cases: the
same-provider guard holds, cross-provider and cross-language same-spot cafes merge,
order-independence holds (google-first still yields one clean_cafe), genuinely different
cafes stay separate, and image linking is correct and complete after `06_link` (every image
on the right clean_cafe, count preserved, no orphans/dangling, idempotent). One real
limitation (4b: cross-language pairs 9–20 m apart do not merge without an englishify
english_name — pure Levenshtein is 0 across scripts) is documented and is expected behavior.
Two bugs surfaced: a clean_cafes schema drift in `01_migrate_db.py` (flagged) and a broken
`test-merge-naebang` db_server invocation (fixed).
