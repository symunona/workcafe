default: start

# ── Init / Setup ─────────────────────────────────────────────────────────────

# Install all missing dependencies (safe to re-run)
[group('Init / Setup')]
install:
    #!/usr/bin/env bash
    set -euo pipefail
    WDIR="$(pwd)"
    VENV="$WDIR/venv/bin/python3"

    echo "── Python venv ──────────────────────────────────────"
    if [ ! -f "$VENV" ]; then
        echo "Creating venv..."
        uv venv "$WDIR/venv"
    fi

    echo "── Python packages ──────────────────────────────────"
    uv pip install -q --python "$VENV" playwright Pillow pyproj requests stem PySocks
    echo "  done"

    echo "── Playwright chromium ──────────────────────────────"
    if ! ls ~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome &>/dev/null; then
        "$WDIR/venv/bin/playwright" install chromium
    else
        echo "  already installed"
    fi

    echo "── Done. Run: just check ────────────────────────────"

# Check all dependencies and services are correctly installed
[group('Init / Setup')]
check:
    #!/usr/bin/env bash
    WDIR="$(pwd)"
    NODE_BIN="$HOME/.nvm/versions/node/v22.21.1/bin"
    VENV="$WDIR/venv/bin/python3"
    G='\033[0;32m'; R='\033[0;31m'; Y='\033[0;33m'; NC='\033[0m'
    ok()   { printf "${G}✓${NC} %s\n" "$1"; }
    fail() { printf "${R}✗${NC} %-45s fix: %s\n  uses: %s\n" "$1" "$2" "$3"; }

    echo ""
    echo "── Python environment ───────────────────────────────"

    if [ -f "$VENV" ]; then ok "venv ($WDIR/venv)"
    else fail "venv missing" \
        "uv venv $WDIR/venv" \
        "all scrapers"; fi

    for pkg in requests pyproj PIL stem socks; do
        label="$pkg"; [ "$pkg" = "PIL" ] && label="Pillow"
        if [ -f "$VENV" ] && "$VENV" -c "import $pkg" 2>/dev/null; then ok "python: $label"
        else
            fix="$WDIR/venv/bin/pip install $label"
            case "$pkg" in
                PIL)  used="image scrapers (scraper_kakao_images_v3, scraper_naver_images_v1, scraper_google_images_v1)" ;;
                stem) used="scraper_google_images_v1.py — Tor NEWNYM circuit rotation" ;;
                socks) used="get_tor_session() — Tor SOCKS5 proxy for osm/google scrapers" ;;
                pyproj) used="spiral grid coordinate math in scrapers" ;;
                *) used="all scrapers (HTTP requests)" ;;
            esac
            fail "python: $label" "$fix" "$used"
        fi
    done

    if [ -f "$VENV" ] && "$VENV" -c "import playwright" 2>/dev/null; then ok "python: playwright"
    else fail "python: playwright" "$WDIR/venv/bin/pip install playwright" "scraper_kakao_v2, scraper_naver (headless browser)"; fi

    if ls ~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome &>/dev/null; then ok "playwright: chromium browser"
    else fail "playwright: chromium" "$WDIR/venv/bin/playwright install chromium" "scraper_kakao_v2, scraper_naver (headless browser scraping)"; fi

    echo ""
    echo "── Node / frontend ──────────────────────────────────"

    if [ -d "$HOME/.nvm/versions/node/v22.21.1" ]; then ok "node v22.21.1"
    else fail "node v22.21.1 missing" "nvm install 22.21.1" "frontend dev server, pnpm"; fi

    if [ -f "$NODE_BIN/pnpm" ]; then ok "pnpm"
    else fail "pnpm missing" "npm install -g pnpm" "frontend (pnpm dev, pnpm install)"; fi

    if [ -d "$WDIR/frontend/node_modules" ]; then ok "frontend node_modules"
    else fail "frontend node_modules" "cd $WDIR/frontend && $NODE_BIN/pnpm install" "frontend dev server (vite)"; fi

    echo ""
    echo "── Go ───────────────────────────────────────────────"

    if /snap/bin/go version &>/dev/null 2>&1 || command -v go &>/dev/null; then ok "go compiler"
    else fail "go missing" "snap install go --classic" "API build (ExecStartPre in workcafe-api.service)"; fi

    echo ""
    echo "── Tor ──────────────────────────────────────────────"

    if [ -f /usr/sbin/tor ] || command -v tor &>/dev/null; then ok "tor binary"
    else fail "tor not installed" "sudo apt install tor" "scraper_osm.py, scraper_google_images_v1.py (anon scraping via SOCKS5)"; fi

    if nc -z 127.0.0.1 9050 2>/dev/null; then ok "tor SOCKS5 proxy (:9050)"
    else fail "tor not running" "sudo systemctl start tor" "get_tor_session() — osm + google image scrapers route through Tor"; fi

    if nc -z 127.0.0.1 9051 2>/dev/null; then ok "tor control port (:9051)"
    else fail "tor control port closed" \
        "sudo sed -i 's/#ControlPort 9051/ControlPort 9051/' /etc/tor/torrc && sudo systemctl reload tor" \
        "stem NEWNYM in scraper_google_images_v1.py (rotates Tor circuit on 429/block)"; fi

    TOR_CTRL=$(cd "$WDIR/scraper" && "$VENV" check_tor.py 2>&1)
    if groups | grep -qw debian-tor; then ok "user in debian-tor group (cookie auth)"
    else fail "user not in debian-tor group" "sudo usermod -aG debian-tor \$USER  then log out/in" "tor cookie auth: read /run/tor/control.authcookie"
    fi

    if [ "$TOR_CTRL" = "ok" ]; then ok "tor NEWNYM (stem auth + circuit rotation)"
    elif [ "$TOR_CTRL" = "no_stem" ]; then fail "tor NEWNYM: stem missing" "$WDIR/venv/bin/pip install stem" "google image scraper: Tor circuit rotation"
    else fail "tor NEWNYM failed: $TOR_CTRL" "sudo usermod -aG debian-tor \$USER then log out/in" "google image scraper: circuit rotation on block"
    fi

    echo ""
    echo "── Data ─────────────────────────────────────────────"

    if [ -f "$WDIR/data/seoul/scraped.db" ]; then ok "scraped.db"
    else fail "scraped.db missing" \
        "ssh c \"gzip -c ~/dev/workcafe/data/seoul/scraped.db\" | gunzip > $WDIR/data/seoul/scraped.db" \
        "API (/api/scraped_cafes), all scrapers (write target)"; fi

    if [ -d "$WDIR/data/seoul" ]; then ok "data/seoul/ dir"
    else fail "data/seoul/ missing" "mkdir -p $WDIR/data/seoul" "scrapers (image + DB storage)"; fi

    if [ -d "$WDIR/scraper/log" ]; then ok "scraper/log/ dir"
    else fail "scraper/log/ missing" "mkdir -p $WDIR/scraper/log" "ralph_loop.py (rotated log files per scraper)"; fi

    echo ""

# Install all systemd user services and enable them
[group('Init / Setup')]
install-services:
    #!/usr/bin/env bash
    set -euo pipefail
    WDIR="$(pwd)"
    UNIT_DIR="$HOME/.config/systemd/user"
    NODE_BIN="$HOME/.nvm/versions/node/v22.21.1/bin"
    PNPM="$NODE_BIN/pnpm"
    mkdir -p "$UNIT_DIR"

    # Create venv if missing
    if [ ! -f "$WDIR/venv/bin/python3" ]; then
        echo "Creating venv..."
        uv venv "$WDIR/venv"
        uv pip install -q --python "$WDIR/venv/bin/python3" playwright Pillow pyproj requests stem
        "$WDIR/venv/bin/python3" -m playwright install chromium
        echo "  venv ready"
    fi
    VENV="$WDIR/venv/bin/python3"

    # Create log dir for scrapers
    mkdir -p "$WDIR/scraper/log"

    # Install frontend deps if missing
    if [ ! -d "$WDIR/frontend/node_modules" ]; then
        echo "Installing frontend deps..."
        cd "$WDIR/frontend" && "$NODE_BIN/pnpm" install
    fi

    write_unit() {
        local name="$1"; local content="$2"
        echo "$content" > "$UNIT_DIR/$name.service"
        echo "  wrote $name.service"
    }

    write_unit workcafe-db-server "[Unit]
    Description=Workcafe DB Server (SQLite socket)
    After=network.target

    [Service]
    Type=simple
    WorkingDirectory=$WDIR/scraper
    Environment="PYTHONPATH=$WDIR/scraper/lib"
    ExecStart=$VENV db_server.py
    Restart=on-failure
    RestartSec=3

    [Install]
    WantedBy=default.target"

    write_unit workcafe-api "[Unit]
    Description=Workcafe API (Go :8090)
    After=network.target

    [Service]
    Type=simple
    WorkingDirectory=$WDIR/api
    ExecStartPre=/snap/bin/go build -o workcafe-api .
    ExecStart=$WDIR/api/workcafe-api
    Restart=on-failure
    RestartSec=5

    [Install]
    WantedBy=default.target"

    write_unit workcafe-frontend "[Unit]
    Description=Workcafe Frontend (Vite :5550)
    After=workcafe-api.service

    [Service]
    Type=simple
    WorkingDirectory=$WDIR/frontend
    Environment=PATH=$NODE_BIN:/usr/local/bin:/usr/bin:/bin
    ExecStart=$PNPM dev
    Restart=on-failure
    RestartSec=5

    [Install]
    WantedBy=default.target"

    for provider in kakao google osm naver; do
        case $provider in
            kakao)  script="places/scraper_kakao_v2.py" ;;
            google) script="places/scraper_google_v2.py" ;;
            osm)    script="places/scraper_osm.py" ;;
            naver)  script="places/scraper_naver.py" ;;
        esac
        write_unit "workcafe-scraper-$provider" "[Unit]
    Description=Workcafe scraper: $provider
    After=network.target

    [Service]
    Type=simple
    WorkingDirectory=$WDIR/scraper
    Environment="PYTHONPATH=$WDIR/scraper/lib"
    ExecStart=$VENV ralph_loop.py $script
    Restart=on-failure
    RestartSec=10

    [Install]
    WantedBy=default.target"
    done

    write_unit workcafe-kakao-images "[Unit]
    Description=Workcafe image scraper: kakao
    After=workcafe-db-server.service
    Requires=workcafe-db-server.service

    [Service]
    Type=simple
    WorkingDirectory=$WDIR/scraper
    Environment="PYTHONPATH=$WDIR/scraper/lib"
    ExecStart=$VENV images/scraper_kakao_images_v3.py
    Restart=on-failure
    RestartSec=30

    [Install]
    WantedBy=default.target"

    write_unit workcafe-naver-images "[Unit]
    Description=Workcafe image scraper: naver
    After=workcafe-db-server.service
    Requires=workcafe-db-server.service

    [Service]
    Type=simple
    WorkingDirectory=$WDIR/scraper
    Environment="PYTHONPATH=$WDIR/scraper/lib"
    ExecStart=$VENV images/scraper_naver_images_v1.py
    Restart=on-failure
    RestartSec=30

    [Install]
    WantedBy=default.target"

    write_unit workcafe-google-images "[Unit]
    Description=Workcafe image scraper: google
    After=workcafe-db-server.service
    Requires=workcafe-db-server.service

    [Service]
    Type=simple
    WorkingDirectory=$WDIR/scraper
    Environment="PYTHONPATH=$WDIR/scraper/lib"
    ExecStart=$VENV images/scraper_google_images_v1.py
    Restart=on-failure
    RestartSec=30

    [Install]
    WantedBy=default.target"

    write_unit workcafe-kakao-metadata "[Unit]
    Description=Workcafe metadata scraper: kakao
    After=workcafe-db-server.service
    Requires=workcafe-db-server.service

    [Service]
    Type=simple
    WorkingDirectory=$WDIR/scraper
    Environment="PYTHONPATH=$WDIR/scraper/lib"
    ExecStart=$VENV places/scraper_kakao_metadata_v1.py
    Restart=on-failure
    RestartSec=60

    [Install]
    WantedBy=default.target"

    write_unit workcafe-naver-metadata "[Unit]
    Description=Workcafe metadata scraper: naver
    After=workcafe-db-server.service
    Requires=workcafe-db-server.service

    [Service]
    Type=simple
    WorkingDirectory=$WDIR/scraper
    Environment="PYTHONPATH=$WDIR/scraper/lib"
    ExecStart=$VENV places/scraper_naver_metadata_v1.py
    Restart=on-failure
    RestartSec=60

    [Install]
    WantedBy=default.target"

    systemctl --user daemon-reload
    systemctl --user enable \
        workcafe-db-server workcafe-api workcafe-frontend \
        workcafe-scraper-kakao workcafe-scraper-google workcafe-scraper-osm workcafe-scraper-naver \
        workcafe-kakao-images workcafe-naver-images workcafe-google-images \
        workcafe-kakao-metadata workcafe-naver-metadata
    echo ""
    echo "Done. Run: just service all start"


# ── Services ─────────────────────────────────────────────────────────────────

# Start web services (API and frontend) for local development
[group('Services')]
start:
    #!/usr/bin/env bash
    cd api && go build -o workcafe-api . && ./workcafe-api &
    API_PID=$!
    trap "kill $API_PID 2>/dev/null" EXIT
    cd frontend && pnpm dev


# Show one-line status for all workcafe services
[group('Services')]
status:
    #!/usr/bin/env bash
    G='\033[0;32m'; R='\033[0;31m'; GBG='\033[42;97m'; NC='\033[0m'
    chk() {
        local label="$1"; local svc="$2"
        if systemctl --user is-active --quiet "$svc"; then
            printf "${G}✓${NC} %s\n" "$label"
        else
            local exit_code
            exit_code=$(systemctl --user show "$svc" --property=ExecMainStatus --value 2>/dev/null)
            if [ "$exit_code" = "0" ]; then
                printf "${GBG} ✓ ${NC} %s\n" "$label"
            else
                printf "${R}✗${NC} %s\n" "$label"
            fi
        fi
    }
    echo "── core ─────────────────────────────────────────────"
    chk db-server  workcafe-db-server
    chk api        workcafe-api
    chk frontend   workcafe-frontend
    echo "── scraper (location data) ──────────────────────────"
    chk kakao      workcafe-scraper-kakao
    chk google     workcafe-scraper-google
    chk osm        workcafe-scraper-osm
    chk naver      workcafe-scraper-naver
    echo "── image-scraper (photos) ───────────────────────────"
    chk kakao-images  workcafe-kakao-images
    chk naver-images  workcafe-naver-images
    chk google-images workcafe-google-images
    echo "── metadata-scraper (website/phone/hours) ───────────"
    chk kakao-metadata workcafe-kakao-metadata
    chk naver-metadata workcafe-naver-metadata

# Kill all managed services
[group('Services')]
kill:
    @just service all stop

# Restart all managed services
[group('Services')]
restart:
    @just service all restart

# Manage services. Usage: just service <target> [start|stop|status|restart]
# Targets:
#   all              — every service
#   scraper          — kakao + google + osm + naver (location scrapers)
#   image-scraper    — kakao-images + naver-images + google-images (photo scrapers)
#   metadata-scraper — kakao-metadata + naver-metadata (metadata scrapers)
#   db-server | api | frontend | kakao | google | osm | naver | kakao-images | naver-images | google-images | kakao-metadata | naver-metadata
[group('Services')]
service target action="status":
    #!/usr/bin/env bash
    ALL="workcafe-db-server workcafe-api workcafe-frontend workcafe-scraper-kakao workcafe-scraper-google workcafe-scraper-osm workcafe-scraper-naver workcafe-kakao-images workcafe-naver-images workcafe-google-images workcafe-kakao-metadata workcafe-naver-metadata"
    SCRAPERS="workcafe-scraper-kakao workcafe-scraper-google workcafe-scraper-osm workcafe-scraper-naver"
    IMAGES="workcafe-kakao-images workcafe-naver-images workcafe-google-images"
    META="workcafe-kakao-metadata workcafe-naver-metadata"
    case "{{target}}" in
      all)              systemctl --user {{action}} $ALL; exit 0 ;;
      scraper)          systemctl --user {{action}} $SCRAPERS; exit 0 ;;
      image-scraper)    systemctl --user {{action}} $IMAGES; exit 0 ;;
      metadata-scraper) systemctl --user {{action}} $META; exit 0 ;;
      db-server)        svc="workcafe-db-server" ;;
      api)              svc="workcafe-api" ;;
      frontend)         svc="workcafe-frontend" ;;
      kakao)            svc="workcafe-scraper-kakao" ;;
      google)           svc="workcafe-scraper-google" ;;
      osm)              svc="workcafe-scraper-osm" ;;
      naver)            svc="workcafe-scraper-naver" ;;
      kakao-images)     svc="workcafe-kakao-images" ;;
      naver-images)     svc="workcafe-naver-images" ;;
      google-images)    svc="workcafe-google-images" ;;
      kakao-metadata)   svc="workcafe-kakao-metadata" ;;
      naver-metadata)   svc="workcafe-naver-metadata" ;;
      *)
        echo "Unknown target: {{target}}"
        echo "Use: all | scraper | image-scraper | metadata-scraper | db-server | api | frontend | kakao | google | osm | naver | kakao-images | naver-images | google-images | kakao-metadata | naver-metadata"
        exit 1 ;;
    esac
    systemctl --user {{action}} "$svc"


# Register systemd timer that runs watchdog every 30 min
[group('Services')]
register-watchdog:
    bash "{{ justfile_directory() }}/scraper/register_watchdog.sh" "{{ justfile_directory() }}"

# Disable and remove the watchdog timer
[group('Services')]
deregister-watchdog:
    #!/usr/bin/env bash
    systemctl --user disable --now workcafe-watchdog.timer 2>/dev/null || true
    rm -f "$HOME/.config/systemd/user/workcafe-watchdog.service"
    rm -f "$HOME/.config/systemd/user/workcafe-watchdog.timer"
    systemctl --user daemon-reload
    echo "Watchdog deregistered."

# Run watchdog immediately (one-shot, outside timer)
[group('Services')]
watchdog-run:
    @cd scraper && ../venv/bin/python watchdog.py

# Reset watchdog restart counter for an image scraper (after manual intervention)
# Usage: just watchdog-reset kakao-images
[group('Services')]
watchdog-reset name:
    @cd scraper && ../venv/bin/python watchdog.py --reset {{name}}


# ── Scrapers ─────────────────────────────────────────────────────────────────

# Run a specific scraper. Usage: just scrape-one [provider] [max_steps]
[group('Scrapers')]
scrape-one provider="kakao" max_steps="100":
    #!/usr/bin/env bash
    source venv/bin/activate
    case "{{provider}}" in
        kakao)  script=scraper/places/scraper_kakao_v2.py ;;
        google) script=scraper/places/scraper_google_v2.py ;;
        naver)  script=scraper/places/scraper_naver.py ;;
        osm)    script=scraper/places/scraper_osm.py ;;
        *) echo "Unknown provider: {{provider}}. Use: kakao, google, naver, osm"; exit 1 ;;
    esac
    PYTHONPATH=scraper/lib python "$script" --max-steps {{max_steps}}

# Download images (v3, with full metadata). Usage: just images [cafe_id]
[group('Scrapers')]
images cafe_id="":
    #!/usr/bin/env bash
    source venv/bin/activate
    if [ -n "{{cafe_id}}" ]; then
        PYTHONPATH=scraper/lib python scraper/images/scraper_kakao_images_v3.py --cafe-id {{cafe_id}}
    else
        PYTHONPATH=scraper/lib python scraper/images/scraper_kakao_images_v3.py
    fi


# ── Data Pipeline ────────────────────────────────────────────────────────────

# Pull ollama models required for normalization (nomic-embed-text, qwen2.5:1.5b)
[group('Data Pipeline')]
pull-models:
    #!/usr/bin/env bash
    source venv/bin/activate
    python data-processing/02_pull_models.py

# Run DB migration to add clean_cafes, cafe_chains tables and new columns
[group('Data Pipeline')]
db-migrate:
    #!/usr/bin/env bash
    source venv/bin/activate
    python data-processing/01_migrate_db.py

# Normalize scraped_cafes into clean_cafes (safe to restart, skips already-processed)
# Options: --embed (add embeddings, slower), --provider kakao/google/naver/osm
[group('Data Pipeline')]
normalize limit="0":
    #!/usr/bin/env bash
    source venv/bin/activate
    PLAY_SOCK=/tmp/workcafe_play_db.sock
    ENG_DB=data/seoul/englishify.db
    if [ "{{limit}}" = "0" ]; then
        python3 data-processing/04_normalize_pipeline.py --socket "$PLAY_SOCK" --englishify-db "$ENG_DB"
    else
        python3 data-processing/04_normalize_pipeline.py --socket "$PLAY_SOCK" --englishify-db "$ENG_DB" --limit {{limit}}
    fi

# Detect chains from scraped_cafes name frequency; writes to cafe_chains on play DB.
# Uses play DB if socket exists, otherwise starts it first.
[group('Data Pipeline')]
detect-chains:
    #!/usr/bin/env bash
    source venv/bin/activate
    PLAY_SOCK=/tmp/workcafe_play_db.sock
    if [ ! -S "$PLAY_SOCK" ]; then
        echo "Play DB not running — starting..."
        bash scripts/start_play_db.sh
    fi
    python3 data-processing/03_detect_chains.py \
        --socket "$PLAY_SOCK" \
        --verbose

# Build/update englishify.db translation cache (Korean→English name lookup).
# Chain cafes filled from cafe_chains (no LLM call). Independent: ollama batch-30.
# Benchmark note: opus-mt-ko-en ~10/s but hallucinates brand transliterations;
# ollama qwen2.5:1.5b wins on quality for cafe names.
# Safe to re-run: idempotent. Output: data/seoul/englishify.db
[group('Data Pipeline')]
englishify:
    #!/usr/bin/env bash
    source venv/bin/activate
    python3 data-processing/05_englishify.py --socket /tmp/workcafe_play_db.sock

# Bulk-update images.belongs_to_cafe_id from scraped_cafes table (run after normalize)
[group('Data Pipeline')]
link-images:
    #!/usr/bin/env bash
    source venv/bin/activate
    python3 data-processing/06_update_image_links.py --socket /tmp/workcafe_play_db.sock

# Dedup raw scraped_cafes in scraped.db (same provider+location: keep latest).
# Manual only — mutates live scraped.db.
[group('Data Pipeline')]
dedup-scraped:
    #!/usr/bin/env bash
    source venv/bin/activate
    echo "WARNING: this mutates scraped.db directly. Continue? [y/N]"
    read confirm; [ "$confirm" = "y" ] || exit 0
    python3 data-processing/00_dedup_raw_cafes.py --socket /tmp/workcafe_db.sock

# Copy scraped.db → clean.db (stops play db server first to avoid conflict)
[group('Data Pipeline')]
db-clean:
    #!/usr/bin/env bash
    PLAY_PID=/tmp/workcafe_play_db.pid
    PLAY_SOCK=/tmp/workcafe_play_db.sock
    if [ -f "$PLAY_PID" ]; then
        OLD_PID=$(cat "$PLAY_PID" 2>/dev/null || true)
        if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
            echo "Stopping play db server (PID $OLD_PID)..."
            kill "$OLD_PID"
            for i in $(seq 1 20); do
                kill -0 "$OLD_PID" 2>/dev/null || break
                sleep 0.5
            done
            kill -0 "$OLD_PID" 2>/dev/null && kill -9 "$OLD_PID" || true
        fi
        rm -f "$PLAY_PID" "$PLAY_SOCK"
    fi
    cp -f data/seoul/scraped.db data/seoul/clean.db
    rm -f data/seoul/clean.db-wal data/seoul/clean.db-shm
    echo "clean.db refreshed from scraped.db"

# Full merge pipeline: scraped.db → clean.db with merged, enriched, linked data
# Steps: copy → migrate schema → detect chains → englishify → normalize → link images
[group('Data Pipeline')]
merge-pipeline:
    #!/usr/bin/env bash
    set -euo pipefail
    PY="$(pwd)/venv/bin/python3"
    B='\033[1m'; G='\033[0;32m'; Y='\033[0;33m'; NC='\033[0m'
    TELEMETRY_PY="$(pwd)/data-processing/pipeline_telemetry.py"
    STEPS=""
    _t() { echo $(($(date +%s%3N) / 1000)); }
    _elapsed() { echo $(( $(_t) - $1 )); }

    PIPELINE_START=$(_t)

    echo ""
    echo -e "${B}━━━ Step 1/6  Copy scraped.db → clean.db ━━━━━━━━━━━━━━━━━━${NC}"
    T=$(_t); just db-clean
    STEPS="copy:$(_elapsed $T)"

    echo ""
    echo -e "${B}━━━ Step 2/6  Start play DB server (clean.db) ━━━━━━━━━━━━━━${NC}"
    T=$(_t); bash scripts/start_play_db.sh
    STEPS="${STEPS},server:$(_elapsed $T)"

    echo ""
    echo -e "${B}━━━ Step 3/6  Migrate schema (clean_cafes, cafe_chains tables) ━━━━${NC}"
    T=$(_t); $PY data-processing/01_migrate_db.py --db data/seoul/clean.db
    STEPS="${STEPS},migrate:$(_elapsed $T)"

    echo ""
    echo -e "${B}━━━ Step 4/6  Detect chains from name frequency ━━━━━━━━━━━━━━━━━━━${NC}"
    T=$(_t); $PY data-processing/03_detect_chains.py --socket /tmp/workcafe_play_db.sock
    STEPS="${STEPS},chains:$(_elapsed $T)"

    echo ""
    echo -e "${B}━━━ Step 5/6  Build/update englishify.db translation cache ━━━━━━━━━━${NC}"
    T=$(_t); $PY data-processing/05_englishify.py --socket /tmp/workcafe_play_db.sock
    STEPS="${STEPS},englishify:$(_elapsed $T)"

    echo ""
    echo -e "${B}━━━ Step 6/6  Merge scraped_cafes → clean_cafes ━━━━━━━━━━━━━━━━━━━━${NC}"
    T=$(_t); $PY data-processing/04_normalize_pipeline.py \
        --db data/seoul/clean.db \
        --socket /tmp/workcafe_play_db.sock \
        --englishify-db data/seoul/englishify.db
    STEPS="${STEPS},normalize:$(_elapsed $T)"

    echo ""
    echo -e "${B}━━━ Step 7/6  Link images → clean_cafes ━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    T=$(_t); $PY data-processing/06_update_image_links.py --socket /tmp/workcafe_play_db.sock
    STEPS="${STEPS},link-images:$(_elapsed $T)"

    echo ""
    echo -e "${B}━━━ Telemetry ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    $PY "$TELEMETRY_PY" \
        --log telemetry.log \
        --start "$PIPELINE_START" \
        --steps "$STEPS" \
        --db data/seoul/clean.db || true

    echo ""
    echo -e "${B}━━━ Restart API ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    just service api restart || true

    echo ""
    echo -e "${G}Done.${NC}"
