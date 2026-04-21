import sys
import os
import sqlite3
import json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'scraper'))
from db_client import DBClient

db_path = "data/seoul/clean-data.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
dbc = DBClient(socket_path="/tmp/workcafe_play_db.sock")

dbc.execute("INSERT INTO clean_cafes (id, name, avg_lat, avg_lon) VALUES ('test-id', 'test', 37.0, 127.0)")
row_dbc = dbc.fetchone("SELECT * FROM clean_cafes WHERE id='test-id'")
print(f"Found via dbc: {row_dbc is not None}")
conn.commit()
row = conn.execute("SELECT * FROM clean_cafes WHERE id='test-id'").fetchone()
print(f"Found via conn: {row is not None}")
dbc.execute("DELETE FROM clean_cafes WHERE id='test-id'")
