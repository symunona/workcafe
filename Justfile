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

    if [ -f "$WDIR/data/seoul/cafedata.db" ]; then ok "cafedata.db"
    else fail "cafedata.db missing" \
        "ssh c \"gzip -c ~/dev/workcafe/data/seoul/cafedata.db\" | gunzip > $WDIR/data/seoul/cafedata.db" \
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
            kakao)  script="scraper_kakao_v2.py" ;;
            google) script="scraper_google_v2.py" ;;
            osm)    script="scraper_osm.py" ;;
            naver)  script="scraper_naver.py" ;;
        esac
        write_unit "workcafe-scraper-$provider" "[Unit]
    Description=Workcafe scraper: $provider
    After=network.target

    [Service]
    Type=simple
    WorkingDirectory=$WDIR/scraper
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
    ExecStart=$VENV scraper_kakao_images_v3.py
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
    ExecStart=$VENV scraper_naver_images_v1.py
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
    ExecStart=$VENV scraper_google_images_v1.py
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
    ExecStart=$VENV scraper_kakao_metadata_v1.py
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
    ExecStart=$VENV scraper_naver_metadata_v1.py
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

# Run all v2 scrapers in parallel (foreground)
[group('Scrapers')]
scrape:
    @echo "Starting all v2 scrapers in parallel..."
    bash -c "source venv/bin/activate && python scraper/scrape_v2.py"

# Run a specific scraper. Usage: just scrape-one [provider] [max_steps]
# Note: google uses v3 (slow/clearnet). Pass provider=google_v3 to be explicit.
[group('Scrapers')]
scrape-one provider="kakao" max_steps="100":
    @echo "Running {{provider}} scraper for {{max_steps}} steps..."
    bash -c "source venv/bin/activate && python scraper/scraper_{{provider}}.py --max-steps {{max_steps}}"

# Download images (v3, with full metadata). Usage: just images [cafe_id]
[group('Scrapers')]
images cafe_id="":
    #!/usr/bin/env bash
    source venv/bin/activate
    if [ -n "{{cafe_id}}" ]; then
        python scraper/scraper_kakao_images_v3.py --cafe-id {{cafe_id}}
    else
        python scraper/scraper_kakao_images_v3.py
    fi


# ── Data Pipeline ────────────────────────────────────────────────────────────

# Pull ollama models required for normalization (nomic-embed-text, qwen2.5:1.5b)
[group('Data Pipeline')]
pull-models:
    #!/usr/bin/env bash
    source venv/bin/activate
    python data-processing/cleaner/02_pull_models.py

# Run DB migration to add clean_cafes, cafe_chains tables and new columns
[group('Data Pipeline')]
db-migrate:
    #!/usr/bin/env bash
    source venv/bin/activate
    python data-processing/cleaner/01_migrate_db.py

# Normalize scraped_cafes into clean_cafes (safe to restart, skips already-processed)
# Options: --embed (add embeddings, slower), --provider kakao/google/naver/osm
[group('Data Pipeline')]
normalize limit="0":
    #!/usr/bin/env bash
    source venv/bin/activate
    cd scraper
    if [ "{{limit}}" = "0" ]; then
        python data-processing/cleaner/04_normalize_pipeline.py
    else
        python data-processing/cleaner/04_normalize_pipeline.py --limit {{limit}}
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
        bash start_play_db.sh
    fi
    python3 data-processing/cleaner/03_detect_chains.py \
        --socket "$PLAY_SOCK" \
        --verbose

# Translate Korean cafe names to English. Chain cafes filled from cafe_chains (no model call).
# Independent cafes: ollama batch-30 (~2.7/s). Benchmark showed opus-mt-ko-en is faster
# (~10/s inference) but hallucinates on Korean brand transliterations ("I'm sorry, I'm sorry"
# for 카페드리옹) — ollama understands the "cafe brand name" context and wins on quality.
[group('Data Pipeline')]
englishify:
    #!/usr/bin/env bash
    source venv/bin/activate
    python3 data-processing/cleaner/05_english_names_bulk.py --backend ollama --batch-size 30

# Benchmark ollama vs opus-mt on 50 random names. Run this if considering switching backends.
[group('Data Pipeline')]
englishify-benchmark:
    #!/usr/bin/env bash
    source venv/bin/activate
    echo "=== ollama batch-30 (current) ==="
    python3 data-processing/cleaner/05_english_names_bulk.py --benchmark --backend ollama --benchmark-size 50
    echo ""
    echo "=== opus-mt-ko-en (faster but hallucinates brand names) ==="
    python3 data-processing/cleaner/05_english_names_bulk.py --benchmark --backend opus --benchmark-size 50

# Generate English names for clean_cafes (runs after normalize)
[group('Data Pipeline')]
english-names:
    #!/usr/bin/env bash
    source venv/bin/activate
    python3 data-processing/cleaner/05_english_names.py

# Bulk-update images.belongs_to_cafe_id from scraped_cafes table (run after normalize)
[group('Data Pipeline')]
link-images:
    #!/usr/bin/env bash
    source venv/bin/activate
    cd scraper && python3 ../data-processing/cleaner/06_update_image_links.py

# Full normalization pass: migrate → normalize → link images → english names
[group('Data Pipeline')]
normalize-all:
    #!/usr/bin/env bash
    source venv/bin/activate
    python3 data-processing/cleaner/01_migrate_db.py
    cd scraper && python3 ../data-processing/cleaner/04_normalize_pipeline.py
    cd scraper && python3 ../data-processing/cleaner/06_update_image_links.py
    python3 data-processing/cleaner/05_english_names.py --chains-only

# Clean up orphaned/incomplete normalization data (resets belongs_to_cafe_id for re-run)
[group('Data Pipeline')]
db-clean:
    #!/usr/bin/env bash
    PLAY_PID=/tmp/workcafe_play_db.pid
    PLAY_SOCK=/tmp/workcafe_play_db.sock
    if [ -f "$PLAY_PID" ]; then
        OLD_PID=$(cat "$PLAY_PID" 2>/dev/null || true)
        if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
            echo "Stopping play db server (PID $OLD_PID) before replacing DB..."
            kill "$OLD_PID"
            for i in $(seq 1 20); do
                kill -0 "$OLD_PID" 2>/dev/null || break
                sleep 0.5
            done
            kill -0 "$OLD_PID" 2>/dev/null && kill -9 "$OLD_PID" || true
        fi
        rm -f "$PLAY_PID" "$PLAY_SOCK"
    fi
    cp -f data/seoul/cafedata.db data/seoul/clean-data.db
    rm -f data/seoul/clean-data.db-wal data/seoul/clean-data.db-shm
    source venv/bin/activate
    printf 'y\n' | python3 data-processing/cleaner/db_clean.py

# Full merge pipeline: dedup raw → reset clean data → merge → link images → stats
[group('Data Pipeline')]
merge-pipeline:
    #!/usr/bin/env bash
    set -euo pipefail
    PY="$(pwd)/venv/bin/python3"
    B='\033[1m'; G='\033[0;32m'; Y='\033[0;33m'; NC='\033[0m'
    TELEMETRY_PY="$(pwd)/data-processing/cleaner/pipeline_telemetry.py"
    STEPS=""
    _t() { echo $(($(date +%s%3N) / 1000)); }   # current unix seconds (integer)
    _elapsed() { echo $(( $(_t) - $1 )); }

    PIPELINE_START=$(_t)

    echo ""
    echo -e "${B}━━━ Step 0/5  Copy scraper DB to clean DB ━━━━━━━━━━━━━━━━━${NC}"
    T=$(_t); just db-clean
    STEPS="${STEPS}db-clean:$(_elapsed $T)"

    echo ""
    echo -e "${B}━━━ Step 1/6  Start play DB server ━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    T=$(_t); bash start_play_db.sh
    STEPS="${STEPS},server:$(_elapsed $T)"

    echo ""
    echo -e "${B}━━━ Step 2/6  Dedup raw scraped_cafes (same provider + location) ━━━${NC}"
    T=$(_t); $PY data-processing/cleaner/00_dedup_raw_cafes.py --db data/seoul/clean-data.db --socket /tmp/workcafe_play_db.sock
    STEPS="${STEPS},dedup:$(_elapsed $T)"

    echo ""
    echo -e "${B}━━━ Step 3/6  Reset clean_cafes + cafe_chains ━━━━━━━━━━━━━${NC}"
    T=$(_t); printf 'y\n' | $PY data-processing/cleaner/db_clean.py --socket /tmp/workcafe_play_db.sock
    STEPS="${STEPS},reset:$(_elapsed $T)"

    echo ""
    echo -e "${B}━━━ Step 3.5/6  Detect chains from name frequency ━━━━━━━━━━━━━━━━━${NC}"
    T=$(_t); $PY data-processing/cleaner/03_detect_chains.py --socket /tmp/workcafe_play_db.sock
    STEPS="${STEPS},detect-chains:$(_elapsed $T)"

    echo ""
    echo -e "${B}━━━ Step 4/6  Merge scraped_cafes → clean_cafes ━━━━━━━━━━━━━━━━━━━${NC}"
    T=$(_t)
    cd scraper && $PY ../data-processing/cleaner/04_normalize_pipeline.py --db ../data/seoul/clean-data.db --socket /tmp/workcafe_play_db.sock
    cd ..
    STEPS="${STEPS},normalize:$(_elapsed $T)"

    echo ""
    echo -e "${B}━━━ Step 5/6  Link images → clean_cafes ━━━━━━━━━━━━━━━━━━━${NC}"
    T=$(_t)
    cd scraper && $PY ../data-processing/cleaner/06_update_image_links.py --socket /tmp/workcafe_play_db.sock
    cd ..
    STEPS="${STEPS},link-images:$(_elapsed $T)"

    echo ""
    echo -e "${B}━━━ Telemetry ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    $PY "$TELEMETRY_PY" \
        --log telemetry.log \
        --start "$PIPELINE_START" \
        --steps "$STEPS" \
        --db data/seoul/clean-data.db || true

    echo ""
    echo -e "${B}━━━ Restart API ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    just service api restart || true

    echo ""
    echo -e "${G}Done.${NC}"
