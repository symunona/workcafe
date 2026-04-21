import sys
import os
import sqlite3
import json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'scraper'))
from normalize.cafe_norm_utils import haversine_m, name_similarity, is_chain_cafe

db_path = "data/seoul/clean-data.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

# Get the two clean cafes
c1 = dict(conn.execute("SELECT * FROM clean_cafes WHERE id='239dcdb9-ca80-415d-bd7f-a63017144f69'").fetchone())
c2 = dict(conn.execute("SELECT * FROM clean_cafes WHERE id='da6525d8-8d64-4548-85ee-1c712a4153df'").fetchone())

print(f"c1: {c1['name']} at {c1['avg_lat']}, {c1['avg_lon']} providers: {c1['providers']}")
print(f"c2: {c2['name']} at {c2['avg_lat']}, {c2['avg_lon']} providers: {c2['providers']}")

dist = haversine_m(c1['avg_lat'], c1['avg_lon'], c2['avg_lat'], c2['avg_lon'])
print(f"Distance: {dist} meters")

print("Checking if c1 provider in c2 providers...")
c1_provs = json.loads(c1['providers'])
c2_provs = json.loads(c2['providers'])
overlap = set(c1_provs).intersection(set(c2_provs))
print(f"Overlap: {overlap}")

# Get the raw cafes for c2
raw2 = [dict(row) for row in conn.execute("SELECT * FROM cafes WHERE belongs_to_cafe_id=?", (c2['id'],)).fetchall()]
for r in raw2:
    print(f"Raw c2 cafe: {r['name']} ({r['provider']}) at {r['lat']}, {r['lon']}")
    dist_raw = haversine_m(r['lat'], r['lon'], c1['avg_lat'], c1['avg_lon'])
    print(f"  Dist to c1: {dist_raw}m")

