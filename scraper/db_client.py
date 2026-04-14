"""
db_client.py — Client for db_server.py
=======================================
Import and use DBClient in scraper scripts instead of sqlite3 directly.

Usage:
    from db_client import DBClient
    dbc = DBClient()
    dbc.execute("INSERT INTO cafes ...", (id, name, ...))
    row  = dbc.fetchone("SELECT * FROM cafes WHERE id=?", (cafe_id,))
    rows = dbc.fetchall("SELECT id FROM cafes WHERE provider=?", ("kakao",))
    val  = dbc.fetchval("SELECT COUNT(*) FROM images WHERE cafe_id=?", (cafe_id,))
    dbc.executemany("INSERT INTO images ...", [(row1,), (row2,)])

All calls are synchronous and blocking. The server serializes all writes.
Retries up to 3× on connection refused (server may be starting up).
"""

import json
import socket
import struct
import time
import logging

from utils import DB_SOCKET_PATH

log = logging.getLogger(__name__)

_CONNECT_RETRIES = 5
_CONNECT_RETRY_DELAY = 2.0


def _request(payload: dict, socket_path: str = DB_SOCKET_PATH) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    header = struct.pack('>I', len(data))

    last_err = None
    for attempt in range(_CONNECT_RETRIES):
        sock = None
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(socket_path)
            sock.sendall(header + data)

            # Read response
            resp_hdr = b''
            while len(resp_hdr) < 4:
                chunk = sock.recv(4 - len(resp_hdr))
                if not chunk:
                    raise ConnectionError("Server closed connection reading response header")
                resp_hdr += chunk

            length = struct.unpack('>I', resp_hdr)[0]
            resp_data = b''
            while len(resp_data) < length:
                chunk = sock.recv(min(65536, length - len(resp_data)))
                if not chunk:
                    raise ConnectionError("Server closed connection reading response body")
                resp_data += chunk

            return json.loads(resp_data.decode('utf-8'))

        except (ConnectionRefusedError, FileNotFoundError) as e:
            last_err = e
            if attempt < _CONNECT_RETRIES - 1:
                log.warning(f"db_server not ready (attempt {attempt+1}/{_CONNECT_RETRIES}): {e}")
                time.sleep(_CONNECT_RETRY_DELAY)
        except Exception as e:
            last_err = e
            raise
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

    raise RuntimeError(f"db_server unavailable after {_CONNECT_RETRIES} attempts: {last_err}")


class DBClient:
    """
    Blocking DB client. Each method makes one round-trip to db_server.
    Thread-safe: each call opens its own socket connection.
    """

    def __init__(self, socket_path: str = DB_SOCKET_PATH):
        self._socket_path = socket_path

    def execute(self, sql: str, params=()):
        """Run INSERT/UPDATE/DELETE. Raises on error."""
        resp = _request({"op": "execute", "sql": sql, "params": list(params)}, self._socket_path)
        if not resp['ok']:
            raise RuntimeError(f"DB execute error: {resp['error']}\nSQL: {sql}")
        return resp

    def executemany(self, sql: str, params_list):
        """Bulk INSERT/UPDATE/DELETE. Raises on error."""
        resp = _request(
            {"op": "executemany", "sql": sql, "params": [list(p) for p in params_list]},
            self._socket_path
        )
        if not resp['ok']:
            raise RuntimeError(f"DB executemany error: {resp['error']}\nSQL: {sql}")
        return resp

    def fetchone(self, sql: str, params=()):
        """Run SELECT, return first row as list, or None."""
        resp = _request({"op": "fetchone", "sql": sql, "params": list(params)}, self._socket_path)
        if not resp['ok']:
            raise RuntimeError(f"DB fetchone error: {resp['error']}\nSQL: {sql}")
        return resp.get('row')

    def fetchall(self, sql: str, params=()):
        """Run SELECT, return all rows as list of lists."""
        resp = _request({"op": "fetchall", "sql": sql, "params": list(params)}, self._socket_path)
        if not resp['ok']:
            raise RuntimeError(f"DB fetchall error: {resp['error']}\nSQL: {sql}")
        return resp.get('rows', [])

    def fetchval(self, sql: str, params=()):
        """Run SELECT, return first column of first row, or None."""
        row = self.fetchone(sql, params)
        return row[0] if row is not None else None

    def close(self):
        pass  # No persistent connection to close
