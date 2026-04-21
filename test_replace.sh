#!/bin/bash
find . -type f -name "*.py" -o -name "*.go" -o -name "*.tsx" -o -name "*.ts" -o -name "*.sh" -o -name "Justfile" | grep -v "node_modules" | grep -v "venv" | xargs sed -i 's/\bcafes\b/scraped_cafes/g'
