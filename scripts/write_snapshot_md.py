#!/usr/bin/env python3
import sys
path, count = sys.argv[1], sys.argv[2]
open(path, 'w').write(
    f"# naebang_test — 방배/내방 1km subset\n\n"
    f"**Area:** 37.492, 126.989 — 1km x 1km block (방배카페거리 + 내방역)\n"
    f"**Cafes:** {count} clean_cafes from 144 scraped\n"
    f"**Purpose:** merge algo regression test — run `just test-merge-naebang`\n"
)
