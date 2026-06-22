# Checkpoint 02 — UI status funnel (2026-06-23)

## Did
- `api/main.go` /api/status: added funnel fields `funnel_merge_queue`, `funnel_merged_cafes`, `funnel_images_total`, `funnel_images_downloaded`.
  - merge_queue = raw_cafes(scraped.db) − merged_scraped(clean.db belongs NOT NULL).
  - image stages ALL from clean.db (tagger+frontend store) so total≥dl≥processed holds. scraped.db trimmed → was giving >100%.
- `SettingsModal.tsx`: "Pipeline" funnel row below summary cards: Raw scraped → Merge queue → Merged ‖ Images → Downloaded → Processed, → arrows, % subs.

## Verified
- go build ok, tsc ok, `just build` ok.
- API LIVE (sandbox-off curl): raw 42047 → queue 258 → merged 29230 ; images 2,642,925 → 2,509,056 (95%) → 2,342,553 (89%). invariant total≥dl≥processed = TRUE. ✓
- Gotcha: 8s status cache served stale body right after restart — re-curl after TTL showed new fields.
- Frontend NOT browser-verified: vite :5550 not reachable in this namespace (sandbox blocks localhost; dev server not up). tsc is the guarantee.
