default: start

# ── Web services ─────────────────────────────────────────────────────────────

start:
    #!/usr/bin/env bash
    cd api && go build -o workcafe-api . && ./workcafe-api &
    API_PID=$!
    trap "kill $API_PID 2>/dev/null" EXIT
    cd frontend && pnpm dev

# ── Managed services (systemd user) ─────────────────────────────────────────

# Install all systemd user services and enable them
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
        python3 -m venv "$WDIR/venv"
        "$WDIR/venv/bin/pip" install -q playwright Pillow pyproj requests
        "$WDIR/venv/bin/playwright" install chromium
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

    systemctl --user daemon-reload
    systemctl --user enable \
        workcafe-db-server workcafe-api workcafe-frontend \
        workcafe-scraper-kakao workcafe-scraper-google workcafe-scraper-osm workcafe-scraper-naver \
        workcafe-kakao-images workcafe-naver-images workcafe-google-images
    echo ""
    echo "Done. Run: just service all start"

# Show one-line status for all workcafe services
status:
    #!/usr/bin/env bash
    for svc in workcafe-db-server workcafe-api workcafe-frontend \
                workcafe-scraper-kakao workcafe-scraper-google workcafe-scraper-osm workcafe-scraper-naver \
                workcafe-kakao-images workcafe-naver-images workcafe-google-images; do
        if systemctl --user is-active --quiet "$svc"; then
            echo "✓ $svc"
        else
            echo "✗ $svc"
        fi
    done

# Stop specific groups of services (scrape or all)
stop target:
    #!/usr/bin/env bash
    if [ "{{target}}" = "scrape" ]; then
        echo "Stopping all scrapers..."
        systemctl --user stop workcafe-scraper-kakao workcafe-scraper-google workcafe-scraper-osm workcafe-scraper-naver workcafe-kakao-images workcafe-naver-images workcafe-google-images
        echo "All scrapers stopped."
    elif [ "{{target}}" = "all" ]; then
        echo "Stopping all workcafe services..."
        systemctl --user stop workcafe-api workcafe-frontend workcafe-scraper-kakao workcafe-scraper-google workcafe-scraper-osm workcafe-scraper-naver workcafe-kakao-images workcafe-naver-images workcafe-google-images
        echo "All services stopped."
    else
        echo "Unknown target: {{target}}. Use 'scrape' or 'all'."
        exit 1
    fi

# Kill all managed services
kill:
    @just stop all

# Restart all managed services
restart:
    #!/usr/bin/env bash
    echo "Restarting all workcafe services..."
    systemctl --user restart workcafe-api workcafe-frontend workcafe-scraper-kakao workcafe-scraper-google workcafe-scraper-osm workcafe-scraper-naver workcafe-kakao-images workcafe-naver-images workcafe-google-images
    echo "All services restarted."

# Usage: just service <target> [start|stop|status|restart]
# Targets: all | kakao | google | osm | naver | imagescraper | naver_images | api | frontend
service target action="status":
    #!/usr/bin/env bash
    case "{{target}}" in
      all)
        systemctl --user {{action}} workcafe-api workcafe-frontend workcafe-scraper-kakao workcafe-scraper-google workcafe-scraper-osm workcafe-scraper-naver workcafe-kakao-images workcafe-naver-images workcafe-google-images
        exit 0 ;;
      kakao)        svc="workcafe-scraper-kakao" ;;
      google)       svc="workcafe-scraper-google" ;;
      osm)          svc="workcafe-scraper-osm" ;;
      naver)        svc="workcafe-scraper-naver" ;;
      imagescraper) svc="workcafe-kakao-images" ;;
      naver_images) svc="workcafe-naver-images" ;;
      api)          svc="workcafe-api" ;;
      frontend)     svc="workcafe-frontend" ;;
      *)
        echo "Unknown target: {{target}}."
        echo "Use: all | kakao | google | osm | naver | imagescraper | naver_images | api | frontend"
        exit 1 ;;
    esac
    systemctl --user {{action}} "$svc"

# ── Manual scrape commands ────────────────────────────────────────────────────

# Run all v2 scrapers in parallel (foreground)
scrape:
    @echo "Starting all v2 scrapers in parallel..."
    bash -c "source venv/bin/activate && python scraper/scrape_v2.py"

# Run a specific scraper. Usage: just scrape-one [provider] [max_steps]
# Note: google uses v3 (slow/clearnet). Pass provider=google_v3 to be explicit.
scrape-one provider="kakao" max_steps="100":
    @echo "Running {{provider}} scraper for {{max_steps}} steps..."
    bash -c "source venv/bin/activate && python scraper/scraper_{{provider}}.py --max-steps {{max_steps}}"

# Download images (v3, with full metadata). Usage: just images [cafe_id]
images cafe_id="":
    #!/usr/bin/env bash
    source venv/bin/activate
    if [ -n "{{cafe_id}}" ]; then
        python scraper/scraper_kakao_images_v3.py --cafe-id {{cafe_id}}
    else
        python scraper/scraper_kakao_images_v3.py
    fi
