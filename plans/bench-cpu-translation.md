# Benchmark: CPU vs GPU translation throughput (qwen2.5:1.5b)

**Date:** 2026-06-23
**Model:** `qwen2.5:1.5b` (Q4_K_M, ~986MB on disk) via ollama @ `localhost:11434`, `/api/generate`
**Hardware:** GTX 1050 Ti (4GB VRAM), 30GB RAM
**Prompt:** per-name, matching `_translate_one()` in `data-processing/05_englishify.py`
("Translate this Korean cafe name to English. Use transliteration for brand names. Return only the English name, nothing else: {kr}"), `num_predict=30`, `temperature=0.1`.

**Sample:** 50 real distinct Korean cafe names pulled READ-ONLY from `data/seoul/scraped.db`, filtered to names NOT already present (with a translation) in `data/seoul/englishify.db` — i.e. a genuinely fresh, uncached batch. Names in `tmp/bench_names.txt`. (Note: current scraped.db contents are Busan-area; 265 untranslated Korean candidates existed, 50 sampled.)

**State during test:** the RAM++ image tagger (`scripts/tag_images_ram.py`, PID 2226235) was running on the GPU the whole time, holding **1742MiB VRAM**. It survived both runs unharmed (RSS and uptime unchanged). Each config was warmed up with one throwaway call (not timed) to load the model into the target backend.

## Results

| Config | Backend placement | Wall (50 names) | names/s | tok/s (gen, from `eval_count`/`eval_duration`) | gen tokens | Memory delta |
|---|---|---|---|---|---|---|
| **CPU-forced** (`options.num_gpu=0`) | size_vram=0, ~1046MiB in **RAM** | 95.6s | **0.523** | **11.1** | 443 | +~1.1GB RAM; VRAM stays at 1746MiB baseline |
| **GPU** (default options) | size_vram=1346MiB fully on **VRAM** | 85.2s | **0.587** | **13.9** | 477 | VRAM 1746 → 3164MiB (ollama proc +1418MiB); coexists with tagger, ~930MiB headroom |

**Surprise:** the GPU is only ~12% faster than CPU here, NOT the 5-10x you'd normally expect. Reason: the GTX 1050 Ti is already compute-saturated by the image tagger, so ollama's kernels get scheduled around it. The GPU model DOES load into VRAM (it fits — 1346MiB model + tagger's 1742MiB = 3164/4096, fits with headroom), but contention erases most of the speedup. **On an idle GPU the gap would be much larger; with the tagger running, CPU vs GPU is nearly a wash.** This validates the plan to run translation on CPU — you lose almost nothing and you leave the GPU entirely to the tagger.

Other notes:
- `keep_alive` default keeps the model resident for 5 min after a call; the warmup call therefore loads it once and subsequent calls reuse it (no per-call reload). Set `keep_alive:0` to evict immediately (used here for cleanup).
- Output quality is identical between configs (same model). A few branch suffixes ("점") leak through on the per-name prompt — same as prod; the real pipeline cleans/retries these via `_clean_en`/`_valid`.

## Latency projection — CPU translation (0.523 names/s ≈ 1.91s/name)

| New names | CPU wall-clock |
|---|---|
| 30 | ~57s (~1 min) |
| 150 | ~287s (~4.8 min) |
| 500 | ~956s (~16 min) |

(GPU-with-tagger would be ~12% faster: 150 names ≈ 4.3 min. Not worth the contention risk.)

Real pipeline note: `05_englishify.py` uses a **batched** prompt (`BATCH_SIZE=10`, one ollama call per 10 names) which amortizes the fixed prompt-eval cost and is typically faster per-name than this per-name benchmark; these per-name numbers are therefore a conservative (upper-bound) latency estimate. Chain pre-pass and google-native pre-pass also fill many names for free before ollama runs, so a real 150-cafe area usually has fewer than 150 names hitting the LLM.

## Recommendation

**Yes — CPU translation is fast enough.** A freshly-scraped ~150-cafe area translates in **under ~5 minutes** on CPU (likely faster in practice due to batching + chain/google pre-passes), with ~1.1GB RAM and zero GPU contention with the tagger. Realistic end-to-end "scrape → visible in app" lag for a new area is dominated by scraping + normalization, with translation adding only a few minutes; run translation CPU-forced (`options.num_gpu=0`) and keep the GPU reserved for the image tagger.

---
### Raw numbers
- CPU: 50 names / 95.59s wall → 0.523 names/s; 443 gen tokens / total eval_duration → 11.1 tok/s; 3135 prompt tokens. RAM +~1.1GB. VRAM unchanged (size_vram=0).
- GPU: 50 names / 85.17s wall → 0.587 names/s; 477 gen tokens → 13.9 tok/s; 3135 prompt tokens. VRAM 1746→3164MiB (model size_vram=1346MiB). Tagger unaffected.
- JSON: `tmp/bench_result_cpu.json`, `tmp/bench_result_gpu.json`. Script: `tmp/bench_translate.py`. Names: `tmp/bench_names.txt`.
