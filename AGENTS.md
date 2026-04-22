# Agent Instructions

Do not sign the code in commits.

Always read all the AGENT.md -s of the current folder before doing anything.

When starting stopping services, always use `just [servicename]`

When you make modifications to a service, check it's previously set state, and restart if affected. (e.g. restart api on frontend/api changes!)

This project uses **bd** (beads) for issue tracking. Run `bd onboard` to get started.

## Database Architecture

Two SQLite databases, each with a distinct role:

| File | Written by | Read by | Contains |
|------|-----------|---------|----------|
| `data/seoul/cafedata.db` | Scrapers (via `db_server` socket) | API `/api/status` metrics only | Raw scraped cafes + images, always live |
| `data/seoul/clean-data.db` | Normalization pipeline (`just merge-pipeline`) | API for all cafe/map queries | Deduplicated clean cafes, cafe chains, merged images |

**Rule:** never query `clean-data.db` for scraper activity metrics — it lags behind by however long since the last pipeline run. Always query `cafedata.db` for rates, counts, and timestamps. The API (`api/main.go`) opens both: `rawDb` for `/api/status`, `db` for everything else.

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

## Temporary Files

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
