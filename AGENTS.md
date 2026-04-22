# Agent Instructions

## Changelog

| Date | Version | What changed |
|------|---------|--------------|
| 2026-04-22 12:00 | v0.6.0 | DB rename: cafedata.db→scraped.db, clean-data.db→clean.db. englishify.db: persistent Korean→English name cache. Pipeline redesign: cp scraped→clean, migrate, chains, englishify, normalize (uses englishify.db lookup), link-images. data-processing/ committed (was gitignored). Stale doc deletion. scraper/AGENTS.md + data-processing/AGENTS.md written. |
| 2026-04-22 09:00 | v0.5.1 | Scraper dir split: images/, places/, lib/ subdirs. scripts/ for root shell scripts. PYTHONPATH=$WDIR/scraper/lib in all service units. scrape-one recipe fixed (was broken). |
| 2026-04-22 08:00 | v0.5.0 | Watchdog system (watchdog.py + systemd timer). Kakao/Naver metadata scrapers v1. Fixed image scraper silent hangs (AbortController wrapping body read; CAFE_TIMEOUT_SECS ref). Major Justfile expansion. Scraper dir reorganized: archive/, tools/, tests/ subdirs. File placement rules added to AGENTS.md. |
| 2026-04-22 04:00 | v0.4.x | Watchdog prototype, register_watchdog.sh, Settings UI watchdog panel, /api/watchdog-status endpoint. |
| 2026-04-21 12:00 | v0.4.0 | Data cleaner pipeline (data-processing/cleaner/). Two-DB architecture: cafedata.db (raw) + clean-data.db (normalized). merge-pipeline just recipe. API split: rawDb for /api/status, db for everything else. |
| 2026-04-20 04:00 | v0.3.x | Google image scraper consent fix, photos tab clicking. POI icon scraper-color ring. URL navigation pan. Chain colors. Dedup script (same provider+location, keep latest). |
| 2026-04-19 08:00 | v0.3.0 | Cafe normalization pipeline + clean_cafes API + CleanApp frontend. belongs_to_cafe_id populated at insert time. Normalization Justfile recipes. |
| 2026-04-19 03:00 | v0.2.x | Settings improvements, Google scraper backfill, image verification. Tor check. Google scraper v3. |
| 2026-04-18 09:00 | v0.2.0 | Systemd service install via Justfile. Service management with just start/stop/restart/status. |
| 2026-04-16 00:00 | v0.1.x | Component updates across frontend, API, scrapers. Various bug fixes. |
| 2026-04-14 04:00 | v0.1.0 | Image compression (JPEG q75/1024px) at scrape time. Grid paging. db_server socket singleton (fixes orphan images). |
| 2026-04-12 05:00 | v0.0.x | health_check.sh: stop scrapers when disk < 2GB. Verbose grid-skip log silence. |
| 2026-03-30 11:00 | v0.0.3 | Separate Naver image scraper. Naver timeout/JSON fix. UI improvements. |
| 2026-03-30 08:00 | v0.0.2 | Scraper settings modal: hourly line chart, provider selector, queue stats. Service kill/restart just recipes. |
| 2026-03-30 05:00 | v0.0.1 | Mobile layout, full-screen viewer, stats modal, search bar fixes. |
| 2026-03-30 03:00 | v0.0.0 | Initial commit. Kakao/Google/Naver/OSM scrapers + Go API + Vite+React frontend. |

---

Do not sign the code in commits.

Always read all the AGENT.md -s of the current folder before doing anything.

Starting stopping services, always use `just [servicename]`

On modifications to a service, check it's previously set state, and restart if affected. Restart api on frontend/api changes or scrapers.


### scripts

python: always use uv for venvs.

design scripts so can be ran and tested on a subset of the data (take a limit param) 
benchmark/see if they created the right results
especially if making scripts with LLM calls.
Point is: always check if your script creates the necessary results

## beads
This project uses **bd** (beads) for issue tracking. Run `bd onboard` to get started.

## Database Architecture

Three SQLite databases, each with a distinct role:

| File | Written by | Read by | Contains |
|------|-----------|---------|----------|
| `data/seoul/scraped.db` | Scrapers (via `db_server` socket) | API `/api/status` metrics only | Raw scraped cafes + images, always live |
| `data/seoul/clean.db` | Pipeline (`just merge-pipeline`) | API for all cafe/map queries | Deduplicated clean cafes, cafe chains, merged images |
| `data/seoul/englishify.db` | `just englishify` | `just normalize` | Korean→English name translation cache (accumulates across runs) |

**Rule:** never query `clean.db` for scraper activity metrics — it lags behind. Always query `scraped.db` for rates, counts, and timestamps. The API (`api/main.go`) opens both: `rawDb` for `/api/status`, `db` for everything else.

See `data-processing/AGENTS.md` for full pipeline details.

## Sub-Agent Docs

| File | Covers |
|------|--------|
| `scraper/AGENTS.md` | Active scrapers, lib/ modules, output schema, failure modes |
| `data-processing/AGENTS.md` | Pipeline steps, DB architecture, englishify.db schema |

## Services & Processes

**All service management goes through `just`.** Never start/stop scrapers or servers by hand.

```bash
just status           # show status of all services
just start <name>     # start a service
just stop <name>      # stop a service
just restart <name>   # restart a service
just logs <name>      # tail logs
```

### Service names

| Service | systemd unit | What it does |
|---------|-------------|-------------|
| `db-server` | `workcafe-db-server` | Unix socket DB proxy at `/tmp/workcafe_db.sock` → `data/seoul/cafedata.db` |
| `api` | `workcafe-api` | Go REST API on `:8090` |
| `frontend` | `workcafe-frontend` | Vite+React dev server on `:5550` |
| `kakao` | `workcafe-scraper-kakao` | Kakao place metadata scraper |
| `google` | `workcafe-scraper-google` | Google Maps metadata scraper |
| `naver` | `workcafe-scraper-naver` | Naver place metadata scraper |
| `osm` | `workcafe-scraper-osm` | OpenStreetMap scraper |
| `kakao-images` | `workcafe-kakao-images` | Kakao photo downloader (`scraper_kakao_images_v3.py`) |
| `naver-images` | `workcafe-naver-images` | Naver photo downloader (`scraper_naver_images_v1.py`) |
| `google-images` | `workcafe-google-images` | Google Maps photo downloader (`scraper_google_images_v1.py`) |
| `kakao-metadata` | `workcafe-kakao-metadata` | Kakao website/phone/hours enricher (`scraper_kakao_metadata_v1.py`) |
| `naver-metadata` | `workcafe-naver-metadata` | Naver website/phone/hours enricher (`scraper_naver_metadata_v1.py`) |

### Diagnosing stuck image scrapers

Image scrapers can hang silently. **Log files lie** — they buffer writes, so the file timestamp may be 5–10 min behind reality. Always check the **systemd journal**:

```bash
journalctl --user -u workcafe-kakao-images --since "1 hour ago" --no-pager | tail -30
journalctl --user -u workcafe-naver-images --since "1 hour ago" --no-pager | tail -30
journalctl --user -u workcafe-google-images --since "1 hour ago" --no-pager | tail -30
```

If a scraper is alive but silent, check what syscall it's blocked on:

```bash
cat /proc/<PID>/wchan          # do_poll / do_select = waiting for network I/O
ss -tp | grep <PID>            # look for CLOSE-WAIT sockets = remote closed, client hung
```

**Known bugs that break self-recovery — fixed 2026-04-22:**

1. **`scraper_naver_images_v1.py:245`** — AbortController was cleared after `fetch()` resolved but before `resp.text()`. Body reads could hang forever. Fixed: AbortController now wraps both `fetch()` and `resp.text()`; added `timeout=45000` to outer `evaluate()` call.

2. **`scraper_kakao_images_v3.py:127`** — `_alarm_handler` referenced undefined `CAFE_TIMEOUT_SECS`. SIGALRM raised `NameError` → swallowed by `except Exception` inside `download_bytes` → watchdog silently broken. Fixed: use `PAGE_TIMEOUT_SECS`.

### Watchdog

`scraper/watchdog.py` runs every 30 min via systemd timer. Checks log recency for all 3 image scrapers. Restarts any that have been silent > 30 min. Writes `data/watchdog-status.json` (served at `/api/watchdog-status`, shown in Settings UI).

```bash
just register-watchdog    # install + enable systemd timer
just deregister-watchdog  # disable + remove timer
just watchdog-run         # run once immediately
just watchdog-reset <name>  # reset auto_restarts counter (e.g. after manual fix)
```

Auto-restart counters reset automatically when the watchdog detects a PID change it didn't cause (manual restart or systemd on-failure recovery).

### Playwright browsers

Naver and Google image scrapers each spawn a Playwright node driver + Chromium. If a renderer is burning CPU for hours with no log output, the browser is stuck (not the Python process). Restart the scraper service — systemd will kill the whole cgroup including child browser processes.

```bash
just restart naver-images
just restart google-images
```

## Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work atomically
bd close <id>         # Complete work
bd dolt push          # Push beads data to remote
```

## File Placement Rules

### Databases

**Only three canonical DBs:** `data/seoul/scraped.db` (raw), `data/seoul/clean.db` (normalized), `data/seoul/englishify.db` (translation cache).

- **Never** create `.db` files in `scraper/`, project root, or any other location.
- Dev/play fixture: `api/play.db` only — it is gitignored.
- If you find a `.db` anywhere else, it is stale — move it to `tmp/` or delete it.

### Project root

Only these file types belong in the project root: `CLAUDE.md`, `AGENTS.md`, `Justfile`, and `*.md` docs.

Shell scripts belong in `scripts/`. **No** `.py`, `.js`, `.sh` one-offs, screenshots, or `.db` files in the root.

### scripts/ directory

All operational shell scripts live here: `setup_nginx.sh`, `start_play_db.sh`, `health_check.sh`, `run_test_pipeline.sh`, `create_play_db.sh`.

### Scraper directory layout

```
scraper/
├── db_server.py     ← service: SQLite socket proxy (root — needs no PYTHONPATH)
├── ralph_loop.py    ← process runner for place scrapers
├── watchdog.py      ← service: image scraper watchdog
├── check_tor.py     ← Tor connectivity check (called by Justfile)
├── register_watchdog.sh
├── images/          ← image scrapers (Playwright/browser heavy)
│   └── scraper_{kakao_images_v3,naver_images_v1,google_images_v1}.py
├── places/          ← place + enrichment scrapers
│   └── scraper_{kakao_v2,google_v2,naver,osm,*_metadata_v1}.py
├── lib/             ← shared imports: db_client, utils, download_utils, image_utils, disk_check
├── archive/         ← superseded versions (old vN files)
├── tools/           ← one-time ran scripts (backfill, migrate, compress, verify)
│   └── each file must have header: # Run once: YYYY-MM-DD. Purpose: ...
├── tests/           ← ad-hoc manual test scripts
└── log/             ← runtime logs (gitignored)
```

**PYTHONPATH:** all scraper systemd units set `PYTHONPATH=$WDIR/scraper/lib` so scripts in `images/` and `places/` can `import db_client`, `import utils` etc. without path hacks.

**When superseding a scraper:** move the old file to `archive/` and update the service table in AGENTS.md.

**Investigation scripts** go in `tmp/` (one-off, delete when done) or `scraper/tests/` (if worth keeping).

### Temporary Files

**ALWAYS** create and use a `tmp/` folder in the workspace root (`workspace_folder/tmp`) for any temporary scripts, screenshots, or intermediate files. Do not clutter the main directory with test scripts or temporary outputs.

## Non-Interactive Shell Commands

**ALWAYS use non-interactive flags** with file operations to avoid hanging on confirmation prompts.

Shell commands like `cp`, `mv`, and `rm` may be aliased to include `-i` (interactive) mode on some systems, causing the agent to hang indefinitely waiting for y/n input.

**Use these forms instead:**
```bash
# Force overwrite without prompting
cp -f source dest           # NOT: cp source dest
mv -f source dest           # NOT: mv source dest
rm -f file                  # NOT: rm file

# For recursive operations
rm -rf directory            # NOT: rm -r directory
cp -rf source dest          # NOT: cp -r source dest
```

**Other commands that may prompt:**
- `scp` - use `-o BatchMode=yes` for non-interactive
- `ssh` - use `-o BatchMode=yes` to fail instead of prompting
- `apt-get` - use `-y` flag
- `brew` - use `HOMEBREW_NO_AUTO_UPDATE=1` env var

<!-- BEGIN BEADS INTEGRATION profile:full hash:d4f96305 -->
## Issue Tracking with bd (beads)

**IMPORTANT**: This project uses **bd (beads)** for ALL issue tracking. Do NOT use markdown TODOs, task lists, or other tracking methods.

### Why bd?

- Dependency-aware: Track blockers and relationships between issues
- Git-friendly: Dolt-powered version control with native sync
- Agent-optimized: JSON output, ready work detection, discovered-from links
- Prevents duplicate tracking systems and confusion

### Quick Start

**Check for ready work:**

```bash
bd ready --json
```

**Create new issues:**

```bash
bd create "Issue title" --description="Detailed context" -t bug|feature|task -p 0-4 --json
bd create "Issue title" --description="What this issue is about" -p 1 --deps discovered-from:bd-123 --json
```

**Claim and update:**

```bash
bd update <id> --claim --json
bd update bd-42 --priority 1 --json
```

**Complete work:**

```bash
bd close bd-42 --reason "Completed" --json
```

### Issue Types

- `bug` - Something broken
- `feature` - New functionality
- `task` - Work item (tests, docs, refactoring)
- `epic` - Large feature with subtasks
- `chore` - Maintenance (dependencies, tooling)

### Priorities

- `0` - Critical (security, data loss, broken builds)
- `1` - High (major features, important bugs)
- `2` - Medium (default, nice-to-have)
- `3` - Low (polish, optimization)
- `4` - Backlog (future ideas)

### Workflow for AI Agents

1. **Check ready work**: `bd ready` shows unblocked issues
2. **Claim your task atomically**: `bd update <id> --claim`
3. **Work on it**: Implement, test, document
4. **Discover new work?** Create linked issue:
   - `bd create "Found bug" --description="Details about what was found" -p 1 --deps discovered-from:<parent-id>`
5. **Complete**: `bd close <id> --reason "Done"`

### Auto-Sync

bd automatically syncs via Dolt:

- Each write auto-commits to Dolt history
- Use `bd dolt push`/`bd dolt pull` for remote sync
- No manual export/import needed!

### Important Rules

- ✅ Use bd for ALL task tracking
- ✅ Always use `--json` flag for programmatic use
- ✅ Link discovered work with `discovered-from` dependencies
- ✅ Check `bd ready` before asking "what should I work on?"
- ❌ Do NOT create markdown TODO lists
- ❌ Do NOT use external issue trackers
- ❌ Do NOT duplicate tracking systems

For more details, see README.md and docs/QUICKSTART.md.

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd dolt push
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds

<!-- END BEADS INTEGRATION -->
