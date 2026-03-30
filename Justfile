default: start

# ── Web services ─────────────────────────────────────────────────────────────

start:
    #!/usr/bin/env bash
    cd api && go build -o workcafe-api . && ./workcafe-api &
    API_PID=$!
    trap "kill $API_PID 2>/dev/null" EXIT
    cd frontend && pnpm dev

# ── Managed services (systemd user) ─────────────────────────────────────────

# Kill all managed services
kill:
    #!/usr/bin/env bash
    echo "Stopping all workcafe services..."
    systemctl --user stop workcafe-api workcafe-frontend workcafe-scraper-kakao workcafe-scraper-google workcafe-scraper-osm workcafe-scraper-naver workcafe-kakao-images workcafe-naver-images
    echo "All services stopped."

# Restart all managed services
restart:
    #!/usr/bin/env bash
    echo "Restarting all workcafe services..."
    systemctl --user restart workcafe-api workcafe-frontend workcafe-scraper-kakao workcafe-scraper-google workcafe-scraper-osm workcafe-scraper-naver workcafe-kakao-images workcafe-naver-images
    echo "All services restarted."

# Usage: just service <target> [start|stop|status|restart]
# Targets: kakao | google | osm | naver | imagescraper | naver_images | api | frontend
service target action="status":
    #!/usr/bin/env bash
    case "{{target}}" in
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
        echo "Use: kakao | google | osm | naver | imagescraper | naver_images | api | frontend"
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


