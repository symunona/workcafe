# Data Quality Report — Cafe Source Analysis

Generated: 2026-04-14

## Counts

| Source | Total | Method |
|--------|-------|--------|
| Kakao  | 13,892 | Keyword search: 카페/커피/브런치/디저트카페 |
| Naver  | 10,509 | Category-based search |
| OSM    | 4,332  | `amenity=cafe` only |
| Google | 1,188  | Partial scrape |

---

## OSM — most precise, most incomplete

All 4,332 are `amenity=cafe` — clean signal. OSM underreports because it's foreign-contributor-driven. Seoul is under-mapped vs local apps. **4k is the floor, not ceiling.**

---

## Naver — moderate noise

| Category | Count | % | Verdict |
|----------|-------|---|---------|
| 카페,디저트 | 6,146 | 58.5% | clean |
| **음식점** | **3,642** | **34.7%** | **mixed** |
| 키즈카페,실내놀이터 | 272 | 2.6% | noise |
| 브런치카페 | 193 | 1.8% | clean |
| 스터디카페 | 101 | 1.0% | clean |
| 서비스,산업 | 53 | 0.5% | noise |

The `음식점` (restaurant) bucket is 3,642 entries. Manual sampling shows ~43% have 커피/카페 in name (1,579/3,642) → actual cafes miscategorized by Naver. The remaining ~2,063 are ambiguous (brunch spots, bakeries, bars). Realistically **~1,500–2,000 of these are genuine noise**.

**Naver true cafe estimate: ~8,500–9,000**

---

## Kakao — highest noise risk

- 97% (13,494/13,892) have **no category stored** in metadata
- Searched with 4 open keywords, not a category filter
- `"커피"` keyword on Kakao Maps returns anything matching the term: convenience stores, hotel lounges, vending spots
- The v2 scraper (`scraper_kakao_v2.py`) uses `searchView?q=<keyword>` — no `category_group_code` filter applied
- Raw `placeList` API response only stores `confirmid`, `name`, `x`, `y` — category data never captured
- The 399 entries with category data (`cate_name_depth1` = `음식점|카페`) are from the older v1 scraper

**Name overlap:** 3,203 names appear in both Kakao and Naver → confirms ~23% of Kakao entries are verified real cafes.

**Sample of ambiguous-named entries (no cafe keyword in name, n=30):**
- Mostly legitimate: coffee chains (Starbucks, Banapresso, Mammoth), dessert shops, patisseries, bakeries
- Noise found: `타이밍 뮤직바` (music bar), some dessert counters/vending-style spots
- Estimated noise rate for this bucket: ~5–10%

**Kakao true cafe estimate: ~10,000–11,500** (inflated by ~2,000–4,000 noise entries)

---

## Conclusion

Your hypothesis is correct in direction. OSM undercounts (foreign-contributor bias), Kakao overcounts (loose keyword search, 4 keyword variants). Naver is the most reliable reference.

**Estimated real sit-down cafes in the scraped region: ~8,000–10,000**

---

## Recommended Fix

Re-query Kakao using `category_group_code=CE7` (Kakao's official cafe/bakery category) instead of open keyword search. Expected result: ~7,000–9,000 entries with clean category signal, eliminating the ~3k noise.

In `scraper_kakao_v2.py`, change the API call from:
```
searchView?q=카페&...
```
to use the category group filter:
```
searchView?q=카페&category_group_code=CE7&...
```
or use Kakao's Local API directly with `category_group_code=CE7`.
