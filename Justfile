default: start

start:
    #!/usr/bin/env bash
    cd api && go build -o workcafe-api . && ./workcafe-api &
    API_PID=$!
    trap "kill $API_PID 2>/dev/null" EXIT
    cd frontend && pnpm dev

# Run all provider scrapers in parallel
scrape:
    @echo "Starting all scrapers in parallel..."
    bash -c "source venv/bin/activate && python scraper/scrape.py"

# Run a specific scraper. Usage: just scrape-one [provider] [max_steps]
scrape-one provider="kakao" max_steps="100":
    @echo "Running {{provider}} scraper for {{max_steps}} steps..."
    bash -c "source venv/bin/activate && python scraper/scraper_{{provider}}.py --max-steps {{max_steps}}"

# Download images for already-scraped cafes. Usage: just images [provider]
# provider: naver | kakao | google | osm | all
images provider="all":
    @echo "Downloading images for provider: {{provider}}"
    bash -c "source ~/.bashrc && source venv/bin/activate && python scraper/scraper_images.py --provider {{provider}}"


