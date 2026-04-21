import sqlite3
import os
import sys

sys.path.insert(0, "data-processing/cleaner")
from cafe_norm_utils import lat_lon_bbox

if os.path.exists("test_clean.db"):
    os.remove("test_clean.db")

lat = 37.4934767
lon = 126.9865297
min_lat, max_lat, min_lon, max_lon = lat_lon_bbox(lat, lon, 500)

conn = sqlite3.connect("data/seoul/cafedata.db")
conn.execute("ATTACH DATABASE 'test_clean.db' AS testdb")

# Create tables
with open("scraper/utils.py") as f:
    pass # We can just create tables by SELECT

conn.execute("CREATE TABLE testdb.scraped_cafes AS SELECT * FROM scraped_cafes WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?", (min_lat, max_lat, min_lon, max_lon))
conn.execute("UPDATE testdb.scraped_cafes SET belongs_to_cafe_id = NULL")
conn.execute("CREATE TABLE testdb.images AS SELECT * FROM images WHERE cafe_id IN (SELECT id FROM testdb.scraped_cafes)")
conn.execute("CREATE TABLE testdb.clean_cafes AS SELECT * FROM clean_cafes LIMIT 0")
conn.execute("CREATE TABLE testdb.cafe_chains AS SELECT * FROM cafe_chains LIMIT 0")
conn.commit()
print("test_clean.db created with", conn.execute("SELECT count(*) FROM testdb.scraped_cafes").fetchone()[0], "cafes")
