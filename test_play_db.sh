#!/bin/bash
# Reset play DB
bash ./create_play_db.sh
# Start DB server if not running
if ! pgrep -f "play_db_server" > /dev/null; then
    bash ./start_play_db.sh
fi
# Run pipeline
python3 scraper/normalize/db_clean.py --socket /tmp/workcafe_play_db.sock <<IN
y
IN
python3 scraper/normalize/04_normalize_pipeline.py --db data/seoul/clean-data.db --socket /tmp/workcafe_play_db.sock
