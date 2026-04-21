#!/bin/bash
source venv/bin/activate

export DB_ABS_PATH="$(pwd)/test_clean.db"

python data-processing/cleaner/01_migrate_db.py --db $DB_ABS_PATH

python scraper/db_server.py --db $DB_ABS_PATH --socket /tmp/test_workcafe_db.sock --pid /tmp/test_workcafe_db.pid &
SERVER_PID=$!
sleep 2

python data-processing/cleaner/04_normalize_pipeline.py --db $DB_ABS_PATH --socket /tmp/test_workcafe_db.sock

kill $SERVER_PID
