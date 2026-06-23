default: start

# Build frontend (public mode, IS_PUBLIC=true) — alias for build-public
build: build-public

# ── Public / Production ───────────────────────────────────────────────────────

# Build frontend with IS_PUBLIC=true (hides admin controls) → frontend/dist/
[group('Public / Production')]
build-public:
    #!/usr/bin/env bash
    set -euo pipefail
    WDIR="$(pwd)"
    NODE_BIN="$HOME/.nvm/versions/node/v22.21.1/bin"
    PNPM="$NODE_BIN/pnpm"
    echo "Building public frontend (VITE_IS_PUBLIC=true)..."
    cd "$WDIR/frontend"
    VITE_IS_PUBLIC=true "$PNPM" build
    echo "Built → $WDIR/frontend/dist"

# Build + apply nginx config for static hosting (run once after setup-nginx)
[group('Public / Production')]
deploy-public: build-public
    #!/usr/bin/env bash
    set -euo pipefail
    WDIR="$(pwd)"
    echo "Applying nginx config..."
    bash "$WDIR/scripts/setup_nginx.sh"
    echo "Stopping frontend dev service (nginx serves static files now)..."
    systemctl --user stop workcafe-frontend 2>/dev/null || true
    systemctl --user disable workcafe-frontend 2>/dev/null || true
    echo ""
    echo "Public deployment done. Serving at https://workcafe.c.tmpx.space"
    echo "API on :13854 (NOT publicly exposed — nginx proxies read-only routes only)"

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
    ExecStartPre=-/usr/bin/pkill -x workcafe-api
    ExecStartPre=/snap/bin/go build -o workcafe-api .
    ExecStart=$WDIR/api/workcafe-api
    Restart=on-failure
    RestartSec=5
    KillMode=control-group

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

# Run a specific scraper. Usage: just scrape-one [provider] [max_steps] [region]
# region selects the spiral center: seoul (default) or busan.
[group('Scrapers')]
scrape-one provider="kakao" max_steps="100" region="seoul":
    #!/usr/bin/env bash
    source venv/bin/activate
    case "{{provider}}" in
        kakao)  script=scraper/places/scraper_kakao_v2.py ;;
        google) script=scraper/places/scraper_google_v2.py ;;
        naver)  script=scraper/places/scraper_naver.py ;;
        osm)    script=scraper/places/scraper_osm.py ;;
        *) echo "Unknown provider: {{provider}}. Use: kakao, google, naver, osm"; exit 1 ;;
    esac
    WORKCAFE_REGION={{region}} PYTHONPATH=scraper/lib python "$script" --max-steps {{max_steps}}

# Scrape photos for a random sample of one region's kakao cafes (focused). Usage: just scrape-region-images busan 6
# Targets cafes by region bbox via --cafe-id, so it ignores the global random queue.
# After it finishes, run `just merge-pipeline` (or the merge daemon) to surface images on the map.
[group('Scrapers')]
scrape-region-images region="busan" n="6":
    #!/usr/bin/env bash
    set -euo pipefail
    source venv/bin/activate
    export PYTHONPATH=scraper/lib
    read LATMIN LATMAX LONMIN LONMAX < <(python -c "import sys,math; sys.path.insert(0,'scraper/lib'); import utils; lat,lon=utils.REGIONS['{{region}}']; r=utils.region_radius_km('{{region}}'); dlat=r/110.574; dlon=r/(111.320*math.cos(math.radians(lat))); print(lat-dlat,lat+dlat,lon-dlon,lon+dlon)")
    IDS=$(sqlite3 data/seoul/scraped.db "SELECT id FROM scraped_cafes WHERE provider='kakao' AND lat BETWEEN $LATMIN AND $LATMAX AND lon BETWEEN $LONMIN AND $LONMAX ORDER BY RANDOM() LIMIT {{n}}")
    [ -z "$IDS" ] && { echo "No kakao cafes in {{region}} yet — scrape places first: just scrape-one kakao 25 {{region}}"; exit 0; }
    for id in $IDS; do
        echo "── images for $id ──"
        timeout 120 python scraper/images/scraper_kakao_images_v3.py --cafe-id "$id" 2>&1 | tail -3
    done
    echo "Done. Surface on map: just merge-pipeline (or let the merge daemon sync+link)."

# Backup all DBs (online, consistent) → data/seoul/backups/<label>-<date>/. Usage: just backup-dbs [label]
[group('Data Pipeline')]
backup-dbs label="manual":
    #!/usr/bin/env bash
    set -euo pipefail
    BK="data/seoul/backups/{{label}}-$(date +%Y-%m-%d)"
    mkdir -p "$BK"
    for db in scraped clean englishify; do
        [ -f "data/seoul/$db.db" ] || continue
        echo "Backing up $db.db ..."
        sqlite3 "data/seoul/$db.db" ".backup '$BK/$db.db'"
    done
    ls -lh "$BK"
    echo "Backed up → $BK"

# Delete ONE region from the DBs (Seoul refused). Dry-run unless confirm=yes. Usage: just clean-region busan [yes]
[group('Data Pipeline')]
clean-region region confirm="no":
    #!/usr/bin/env bash
    source venv/bin/activate
    FLAG=""; [ "{{confirm}}" = "yes" ] && FLAG="--confirm"
    python data-processing/clean_region.py --region {{region}} $FLAG

# Run the streaming merge daemon in the foreground (Ctrl+C to stop). Usage: just merge-daemon
[group('Data Pipeline')]
merge-daemon:
    #!/usr/bin/env bash
    source venv/bin/activate
    PYTHONPATH=scraper/lib python data-processing/merge_daemon.py

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

# Overwrite clean_cafes.english_name with Google's native English name where Google
# already scraped a Latin-script name (beats LLM translations like Dahan-Gang).
# Dry-run by default. Pass --apply to commit. Safe: only touches english_name column.
[group('Data Pipeline')]
fix-google-english-names *args:
    #!/usr/bin/env bash
    source venv/bin/activate
    python3 scripts/fix_google_english_names.py {{args}}

# Create a spatial subset of scraped.db for fast pipeline testing.
# Extracts scraped_cafes within a blocksize×blocksize meter square around center.
# belongs_to_cafe_id reset to NULL so pipeline runs fresh on subset.
# Example: just subset 37.492 126.989 1000 data/seoul/subset_test.db
[group('Data Pipeline')]
subset lat lng blocksize="1000" target="data/seoul/subset.db":
    #!/usr/bin/env bash
    source venv/bin/activate
    python3 scripts/create_subset.py --lat {{lat}} --lng {{lng}} --blocksize {{blocksize}} {{target}}

# Validate merge quality against known same-place cafe groups in the 방배카페거리 test area.
# Run after merge-pipeline to check if merges are correct.
[group('Data Pipeline')]
test-pipeline db="data/seoul/clean.db":
    #!/usr/bin/env bash
    source venv/bin/activate
    python3 scripts/test_merge.py --db {{db}}

# Fast merge quality test for the 방배/내방 area.
# Builds a 1km subset (~144 cafes), runs mini-pipeline, checks merge correctness.
# Uses a dedicated socket/pidfile so it never conflicts with the production play DB.
# Takes ~30s total.
[group('Data Pipeline')]
test-merge-naebang:
    #!/usr/bin/env bash
    set -euo pipefail
    source venv/bin/activate

    PY="$(pwd)/venv/bin/python3"
    SUBSET_DB="/tmp/naebang_scraped.db"
    CLEAN_DB="/tmp/naebang_clean.db"
    SOCK="/tmp/naebang_test.sock"
    PID_FILE="/tmp/naebang_test.pid"
    ENG_DB="$(pwd)/data/seoul/englishify.db"

    B='\033[1m'; NC='\033[0m'

    # ── Guard ─────────────────────────────────────────────────────────────────
    if [ -S "$SOCK" ]; then
        echo -e "\n\033[1;31mERROR: Test DB socket already exists: $SOCK\033[0m"
        echo "       Another test pipeline is running. Kill PID \$(cat $PID_FILE 2>/dev/null) or:"
        echo "       rm -f $SOCK $PID_FILE"
        exit 1
    fi
    if [ -f "${CLEAN_DB}.pipeline.lock" ]; then
        echo -e "\n\033[1;31mERROR: Pipeline lock exists: ${CLEAN_DB}.pipeline.lock\033[0m"
        echo "       rm ${CLEAN_DB}.pipeline.lock"
        exit 1
    fi

    # ── 1. Create subset ──────────────────────────────────────────────────────
    echo -e "\n${B}── Step 1/4  Build 1km subset around 방배/내방역 ──────────────────${NC}"
    $PY scripts/create_subset.py --lat 37.492 --lng 126.989 --blocksize 1000 "$SUBSET_DB"

    # ── 2. Copy to clean DB + start server ───────────────────────────────────
    echo -e "\n${B}── Step 2/4  Migrate + detect chains ────────────────────────────${NC}"
    cp -f "$SUBSET_DB" "$CLEAN_DB"
    rm -f "${CLEAN_DB}-wal" "${CLEAN_DB}-shm"

    # Stop any leftover test server
    if [ -f "$PID_FILE" ]; then
        OLD=$(cat "$PID_FILE" 2>/dev/null || true)
        [ -n "$OLD" ] && kill "$OLD" 2>/dev/null || true
        rm -f "$PID_FILE" "$SOCK"
    fi

    cd scraper
    nohup $PY db_server.py \
        --db "$CLEAN_DB" --socket "$SOCK" --pid-file "$PID_FILE" --replace \
        > /tmp/naebang_db_server.log 2>&1 &
    cd ..

    # Wait for socket
    for i in $(seq 1 20); do [ -S "$SOCK" ] && break; sleep 0.3; done
    [ -S "$SOCK" ] || { echo "ERROR: test db_server did not start"; exit 1; }

    $PY data-processing/01_migrate_db.py --db "$CLEAN_DB"
    $PY data-processing/03_detect_chains.py --socket "$SOCK"

    # ── 3. Normalize ──────────────────────────────────────────────────────────
    echo -e "\n${B}── Step 3/4  Normalize (merge) ───────────────────────────────────${NC}"
    $PY data-processing/04_normalize_pipeline.py \
        --db "$CLEAN_DB" --socket "$SOCK" --englishify-db "$ENG_DB" --no-backup

    # Stop test server
    [ -f "$PID_FILE" ] && kill "$(cat "$PID_FILE")" 2>/dev/null || true
    rm -f "$PID_FILE" "$SOCK"

    # ── 4. Publish to history so the map can load it ─────────────────────────
    SNAPSHOT="$(pwd)/data/seoul/history/clean_naebang_test.db"
    sqlite3 "$CLEAN_DB" "PRAGMA wal_checkpoint(TRUNCATE);" 2>/dev/null || true
    cp -f "$CLEAN_DB" "$SNAPSHOT"
    rm -f "${SNAPSHOT}-wal" "${SNAPSHOT}-shm"

    CAFE_COUNT=$(sqlite3 "$SNAPSHOT" "SELECT COUNT(*) FROM clean_cafes" 2>/dev/null || echo "?")
    $PY scripts/write_snapshot_md.py "$(pwd)/data/seoul/history/clean_naebang_test.md" "$CAFE_COUNT"
    echo -e "${B}Snapshot published → data/seoul/history/clean_naebang_test.db${NC} ($CAFE_COUNT cafes)"

    # ── 5. Test ───────────────────────────────────────────────────────────────
    echo -e "\n${B}── Step 5/5  Check merge quality ─────────────────────────────────${NC}"
    $PY scripts/test_merge.py --db "$CLEAN_DB"

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
    [ -S /tmp/workcafe_play_db.sock ] || bash scripts/start_play_db.sh
    python3 data-processing/06_update_image_links.py --socket /tmp/workcafe_play_db.sock

# Re-download images with file_size=-1 (failed downloads from old scrapers, clean.db only).
# Run per-provider. Safe to restart — skips files already on disk.
[group('Data Pipeline')]
redownload-failed provider="kakao":
    #!/usr/bin/env bash
    source venv/bin/activate
    case "{{provider}}" in
      kakao)  python3 scripts/redownload_kakao_images.py ;;
      naver)  python3 scripts/redownload_naver_images.py ;;
      google) python3 scripts/redownload_google_images.py ;;
      *) echo "Unknown provider: {{provider}}. Use kakao, naver, or google." && exit 1 ;;
    esac

# Dedup raw scraped_cafes in scraped.db (same provider+location: keep latest).
# Manual only — mutates live scraped.db.
[group('Data Pipeline')]
dedup-scraped:
    #!/usr/bin/env bash
    source venv/bin/activate
    echo "WARNING: this mutates scraped.db directly. Continue? [y/N]"
    read confirm; [ "$confirm" = "y" ] || exit 0
    python3 data-processing/00_dedup_raw_cafes.py --socket /tmp/workcafe_db.sock

# Sync new scraped_cafes + images from scraped.db into clean.db (additive, idempotent).
[group('Data Pipeline')]
sync-from-scraped:
    #!/usr/bin/env bash
    source venv/bin/activate
    python3 data-processing/00_sync_from_scraped.py

# DESTRUCTIVE: overwrite clean.db from scraped.db — wipes all merged/normalized data.
# Requires explicit confirmation. Use merge-pipeline for incremental updates.
[group('Data Pipeline')]
db-clean:
    #!/usr/bin/env bash
    echo ""
    echo -e "\033[1;31m⚠  WARNING: This will OVERWRITE clean.db with a raw copy of scraped.db.\033[0m"
    echo "   All merged clean_cafes, chains, tags, and image links will be LOST."
    echo ""
    printf "   Type 'yes' to confirm: "
    read -r CONFIRM
    if [ "$CONFIRM" != "yes" ]; then
        echo "Aborted."
        exit 1
    fi
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

# Incremental merge pipeline: processes only new scraped_cafes (belongs_to_cafe_id IS NULL).
# Does NOT overwrite clean.db. Run `just db-clean` first only for a full rebuild.
# Steps: start server → migrate schema → detect chains → englishify → normalize → link images
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

    # ── Cleanup trap: stop play DB server on exit/error/Ctrl+C ───────────────
    PLAY_SOCK=/tmp/workcafe_play_db.sock
    _stop_play_db() {
        local pid
        pid=$(cat /tmp/workcafe_play_db.pid 2>/dev/null || true)
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            echo "Stopping play DB server (PID $pid)..."
            kill "$pid" 2>/dev/null || true
        fi
        rm -f /tmp/workcafe_play_db.sock /tmp/workcafe_play_db.pid
    }
    trap '_stop_play_db' EXIT

    # ── Guard: refuse if play DB socket or pipeline lock already exists ───────
    LOCK_FILE=data/seoul/clean.db.pipeline.lock
    if [ -S "$PLAY_SOCK" ]; then
        echo -e "\n\033[1;31mERROR: Play DB socket already exists: $PLAY_SOCK\033[0m"
        echo "       Another pipeline is likely running. Kill it first:"
        echo "       kill \$(cat /tmp/workcafe_play_db.pid 2>/dev/null)"
        echo "       rm -f $PLAY_SOCK"
        exit 1
    fi
    if [ -f "$LOCK_FILE" ]; then
        echo -e "\n\033[1;31mERROR: Pipeline lock exists: $LOCK_FILE\033[0m"
        echo "       Contents: \$(cat $LOCK_FILE)"
        echo "       If the previous run crashed, remove it manually:"
        echo "       rm $LOCK_FILE"
        exit 1
    fi

    echo ""
    echo -e "${B}━━━ Step 1/7  Sync new rows scraped.db → clean.db ━━━━━━━━━━━━━━━━━━${NC}"
    T=$(_t); $PY data-processing/00_sync_from_scraped.py
    STEPS="sync:$(_elapsed $T)"

    echo ""
    echo -e "${B}━━━ Step 2/7  Start play DB server (clean.db) ━━━━━━━━━━━━━━${NC}"
    T=$(_t); bash scripts/start_play_db.sh
    STEPS="${STEPS},server:$(_elapsed $T)"

    echo ""
    echo -e "${B}━━━ Step 3/7  Migrate schema (clean_cafes, cafe_chains tables) ━━━━${NC}"
    T=$(_t); $PY data-processing/01_migrate_db.py --db data/seoul/clean.db
    STEPS="${STEPS},migrate:$(_elapsed $T)"

    echo ""
    echo -e "${B}━━━ Step 4/7  Detect chains from name frequency ━━━━━━━━━━━━━━━━━━━${NC}"
    T=$(_t); $PY data-processing/03_detect_chains.py --socket /tmp/workcafe_play_db.sock
    STEPS="${STEPS},chains:$(_elapsed $T)"

    echo ""
    echo -e "${B}━━━ Step 5/7  Build/update englishify.db translation cache ━━━━━━━━━━${NC}"
    T=$(_t); $PY data-processing/05_englishify.py --socket /tmp/workcafe_play_db.sock
    STEPS="${STEPS},englishify:$(_elapsed $T)"

    echo ""
    echo -e "${B}━━━ Step 6/7  Merge scraped_cafes → clean_cafes ━━━━━━━━━━━━━━━━━━━━${NC}"
    T=$(_t); $PY data-processing/04_normalize_pipeline.py \
        --db data/seoul/clean.db \
        --socket /tmp/workcafe_play_db.sock \
        --englishify-db data/seoul/englishify.db
    STEPS="${STEPS},normalize:$(_elapsed $T)"

    echo ""
    echo -e "${B}━━━ Step 7/7  Link images → clean_cafes ━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
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

# ── Image Tagging ─────────────────────────────────────────────────────────────

# Mark images missing from disk as file_size=-1 in clean.db so taggers/scrapers skip them.
# Safe: no deletes, fully reversible by re-scraping + merge-pipeline.
# Add --dry-run to preview without writing.
[group('Image Tagging')]
cleanup-ghost-images-clean-db *args:
    #!/usr/bin/env bash
    set -euo pipefail
    "$(pwd)/venv/bin/python3" scripts/cleanup_ghost_images_clean_db.py {{args}}

# Wipe image_tags rows from a snapshot DB (never touches clean.db)
# Usage: just clean-image-tags data/seoul/history/clean_tags_t25_n100_2026-04-23.db
[group('Image Tagging')]
clean-image-tags db:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "Deleting all rows from image_tags in {{db}}..."
    sqlite3 {{db}} "DELETE FROM image_tags;"
    COUNT=$(sqlite3 {{db}} "SELECT COUNT(*) FROM image_tags;")
    echo "Done. Rows remaining: $COUNT"

# Create a snapshot from clean.db, tag it with CLIP, roll up to clean_cafes.tags.
# clean.db is never modified.
# n: number of clean cafes (integer or 'all')
# threshold: min cosine similarity stored (e.g. 0.22 / 0.25 / 0.27)
[group('Image Tagging')]
tag-images n threshold:
    #!/usr/bin/env bash
    set -euo pipefail
    PY="$(pwd)/venv/bin/python3"

    echo "━━━ Creating snapshot (n={{n}}, threshold={{threshold}}) ━━━"
    SNAPSHOT=$("$PY" scripts/create_tag_snapshot.py --n {{n}} --threshold {{threshold}} | tail -1)
    echo "Snapshot: $SNAPSHOT"

    echo ""
    echo "━━━ Tagging images ━━━"
    "$PY" scripts/tag_images_clip.py --n all --threshold {{threshold}} --db "$SNAPSHOT"

    echo ""
    echo "━━━ Rollup image_tags → clean_cafes.tags ━━━"
    "$PY" scripts/tag_cafes_rollup.py --db "$SNAPSHOT"

    echo ""
    echo "Done: $SNAPSHOT"

# Experiment 1 — threshold 0.22 (broad, captures most)
[group('Image Tagging')]
tag-images-experiment-1:
    just tag-images 100 0.22

# Experiment 2 — threshold 0.25 (balanced)
[group('Image Tagging')]
tag-images-experiment-2:
    just tag-images 100 0.25

# Experiment 3 — threshold 0.27 (strict, high confidence only)
[group('Image Tagging')]
tag-images-experiment-3:
    just tag-images 100 0.27

    echo ""
    echo "━━━ Rollup image_tags → clean_cafes.tags ━━━"
    "$PY" scripts/tag_cafes_rollup.py --db "$SNAPSHOT"

    echo ""
    echo "Done: $SNAPSHOT"

# Tag all images in clean.db with RAM+ (continuous — skips already-tagged, safe to restart)
# Runs in tmux session "image-pipeline". Skips if already running.
[group('Data Pipeline')]
image-pipeline vit="swin_large":
    #!/usr/bin/env bash
    set -euo pipefail
    SESSION="image-pipeline"
    if pgrep -f "tag_images_ram.py" > /dev/null; then
        echo "image-pipeline already running (PID $(pgrep -f tag_images_ram.py | head -1)). Attach with: tmux attach -t $SESSION"
        exit 0
    fi
    PY="$(pwd)/venv/bin/python3"
    WDIR="$(pwd)"
    CMD="cd '$WDIR' && source venv/bin/activate && echo '━━━ RAM+ image-pipeline on clean.db ({{vit}}) ━━━' && PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True '$PY' scripts/tag_images_ram.py --n all --vit {{vit}} --from-db data/seoul/clean.db"
    tmux new-session -d -s "$SESSION" -x 220 -y 50 2>/dev/null || tmux new-window -t "$SESSION"
    tmux send-keys -t "$SESSION" "$CMD" Enter
    echo "Started in tmux session '$SESSION'. Attach with: tmux attach -t $SESSION"

# Tag images with RAM+ on a snapshot (for experiments — does not touch clean.db)
[group('Data Pipeline')]
tag-images-ram n="100" vit="swin_large":
    #!/usr/bin/env bash
    set -euo pipefail
    PY="$(pwd)/venv/bin/python3"

    echo "━━━ Creating RAM snapshot (n={{n}}) ━━━"
    SNAPSHOT=$("$PY" scripts/create_tag_snapshot.py --n {{n}} | tail -1)
    echo "Snapshot: $SNAPSHOT"

    echo ""
    echo "━━━ Tagging images with RAM+ ({{vit}}) ━━━"
    PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True "$PY" scripts/tag_images_ram.py --n all --vit {{vit}} --from-db "$SNAPSHOT"

    echo ""
    echo "Done: $SNAPSHOT"
