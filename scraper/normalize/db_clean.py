#!/usr/bin/env python3
import sys
import os
import argparse
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from db_client import DBClient

parser = argparse.ArgumentParser()
parser.add_argument("--socket", default="/tmp/workcafe_db.sock", help="Unix socket path")
args = parser.parse_args()

dbc = DBClient(socket_path=args.socket)
r1 = dbc.fetchval('SELECT COUNT(*) FROM clean_cafes')
r2 = dbc.fetchval('SELECT COUNT(*) FROM cafe_chains')
r3 = dbc.fetchval('SELECT COUNT(*) FROM cafes WHERE belongs_to_cafe_id IS NOT NULL')
print(f'Before: clean_cafes={r1}  chains={r2}  cafes_linked={r3}')
confirm = input('Reset all normalization data? [y/N] ')
if confirm.strip().lower() == 'y':
    dbc.execute('UPDATE cafes SET belongs_to_cafe_id = NULL, name_embedding = NULL')
    dbc.execute('UPDATE images SET belongs_to_cafe_id = NULL')
    dbc.execute('DELETE FROM clean_cafes')
    dbc.execute('DELETE FROM cafe_chains')
    print('Reset done. Run: just normalize-all')
else:
    print('Aborted.')
