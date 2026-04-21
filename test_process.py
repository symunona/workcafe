import sys
import os
import sqlite3
import json
import importlib.util

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'scraper'))

spec = importlib.util.spec_from_file_location("normalize_pipeline", "scraper/normalize/04_normalize_pipeline.py")
normalize_pipeline = importlib.util.module_from_spec(spec)
sys.modules["normalize_pipeline"] = normalize_pipeline
spec.loader.exec_module(normalize_pipeline)
process_cafe = normalize_pipeline.process_cafe

from db_client import DBClient

db_path = "data/seoul/clean-data.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
dbc = DBClient(socket_path="/tmp/workcafe_play_db.sock")

# Get raw cafes for both
raw_google = dict(conn.execute("SELECT * FROM cafes WHERE id='google_0x357ca1a4b27c7f01:0x67c8d0f1dd3bb2f5'").fetchone())
raw_kakao = dict(conn.execute("SELECT * FROM cafes WHERE id='kakao_27346318'").fetchone())
raw_naver = dict(conn.execute("SELECT * FROM cafes WHERE id='naver_37411185'").fetchone())

# Reset their belongs_to_cafe_id
dbc.execute("UPDATE cafes SET belongs_to_cafe_id = NULL WHERE id IN (?, ?, ?)", (raw_google['id'], raw_kakao['id'], raw_naver['id']))
# Delete from clean_cafes
dbc.execute("DELETE FROM clean_cafes")
dbc.execute("DELETE FROM cafe_chains")
conn.commit()

# Process google first
clean_id1, created1 = process_cafe(conn, dbc, raw_google, ["Starbucks"], False)
print(f"Processed Google: clean_id={clean_id1}, created={created1}")

# Process kakao second
clean_id2, created2 = process_cafe(conn, dbc, raw_kakao, ["Starbucks"], False)
print(f"Processed Kakao: clean_id={clean_id2}, created={created2}")

# Process naver third
clean_id3, created3 = process_cafe(conn, dbc, raw_naver, ["Starbucks"], False)
print(f"Processed Naver: clean_id={clean_id3}, created={created3}")

if clean_id1 == clean_id2 == clean_id3:
    print("SUCCESS: They merged!")
else:
    print("FAILED: They did not merge!")

