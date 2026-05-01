"""
db_server.py — Centralized SQLite access server
================================================
Singleton process. All scrapers connect via Unix socket.
Run: python db_server.py

Protocol over Unix socket:
  Request:  4-byte BE uint32 length + UTF-8 JSON payload
  Response: 4-byte BE uint32 length + UTF-8 JSON payload

Request ops:
  {"op":"execute",     "sql":"...", "params":[...]}
  {"op":"executemany", "sql":"...", "params":[[...],[...]]}
  {"op":"fetchone",    "sql":"...", "params":[...]}
  {"op":"fetchall",    "sql":"...", "params":[...]}

Response:
  {"ok":true,  "row":[...], "rows":[[...]], "rowcount":N, "lastrowid":N}
  {"ok":false, "error":"..."}

All writes serialized under _db_lock. Reads also use lock (single connection).
SIGTERM triggers graceful shutdown after active requests finish.
"""

import os
import sys
import json
import socket
import struct
import signal
import logging
import sqlite3
import threading
import argparse

_HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(_HERE)
sys.path.insert(0, os.path.join(_HERE, 'lib'))

from utils import DB_PATH, DB_SOCKET_PATH, DB_PID_FILE, init_tables

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler("log/db_server.log"),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger(__name__)

_db_lock = threading.Lock()
_conn: sqlite3.Connection = None
_shutdown = threading.Event()


def _recv_exact(sock, n: int) -> bytes | None:
    buf = b''
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def _recv_msg(sock) -> bytes | None:
    hdr = _recv_exact(sock, 4)
    if hdr is None:
        return None
    length = struct.unpack('>I', hdr)[0]
    return _recv_exact(sock, length)


def _send_msg(sock, data: bytes):
    try:
        sock.sendall(struct.pack('>I', len(data)) + data)
    except Exception:
        pass


def _handle_client(client_sock):
    try:
        msg = _recv_msg(client_sock)
        if msg is None:
            return

        try:
            req = json.loads(msg.decode('utf-8'))
        except json.JSONDecodeError as e:
            _send_msg(client_sock, json.dumps({"ok": False, "error": f"JSON: {e}"}).encode())
            return

        op     = req.get('op', '')
        sql    = req.get('sql', '')
        params = req.get('params', [])

        with _db_lock:
            try:
                cur = _conn.cursor()
                if op == 'execute':
                    cur.execute(sql, params)
                    _conn.commit()
                    resp = {"ok": True, "rowcount": cur.rowcount, "lastrowid": cur.lastrowid}
                elif op == 'executemany':
                    cur.executemany(sql, params)
                    _conn.commit()
                    resp = {"ok": True, "rowcount": cur.rowcount}
                elif op == 'fetchone':
                    cur.execute(sql, params)
                    row = cur.fetchone()
                    resp = {"ok": True, "row": list(row) if row is not None else None}
                elif op == 'fetchall':
                    cur.execute(sql, params)
                    resp = {"ok": True, "rows": [list(r) for r in cur.fetchall()]}
                else:
                    resp = {"ok": False, "error": f"Unknown op: {op!r}"}
            except sqlite3.Error as e:
                try:
                    _conn.rollback()
                except Exception:
                    pass
                resp = {"ok": False, "error": str(e)}
                log.error(f"DB error op={op!r} sql={sql[:80]!r}: {e}")
            except Exception as e:
                resp = {"ok": False, "error": f"Unexpected: {e}"}
                log.error(f"Handler error: {e}", exc_info=True)

    except Exception as e:
        resp = {"ok": False, "error": f"Server error: {e}"}
        log.error(f"Client handler error: {e}", exc_info=True)

    _send_msg(client_sock, json.dumps(resp, ensure_ascii=False).encode('utf-8'))
    try:
        client_sock.close()
    except Exception:
        pass


def _check_existing(pid_file: str) -> int | None:
    if not os.path.exists(pid_file):
        return None
    try:
        with open(pid_file) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return pid
    except (ValueError, ProcessLookupError, PermissionError):
        return None


def main():
    parser = argparse.ArgumentParser(description='Workcafe DB server')
    parser.add_argument('--db',     default=DB_PATH,      help='SQLite DB path')
    parser.add_argument('--socket', default=DB_SOCKET_PATH, help='Unix socket path')
    parser.add_argument('--pid-file', default=DB_PID_FILE, help='PID file path')
    parser.add_argument('--replace', action='store_true',  help='Kill existing server if running')
    parser.add_argument('--unsafe-any-db', action='store_true', dest='unsafe_any_db', help='Skip scraped.db path safety check')
    args = parser.parse_args()

    pid_file = args.pid_file

    existing = _check_existing(pid_file)
    if existing:
        if args.replace:
            log.info(f"Killing existing server PID {existing}")
            try:
                os.kill(existing, signal.SIGTERM)
                import time; time.sleep(2)
            except Exception:
                pass
        else:
            log.error(f"Server already running (PID {existing}). Use --replace.")
            sys.exit(1)

    with open(pid_file, 'w') as f:
        f.write(str(os.getpid()))

    global _conn
    _conn = sqlite3.connect(args.db, timeout=60, check_same_thread=False)
    _conn.execute('PRAGMA journal_mode=WAL')
    _conn.execute('PRAGMA busy_timeout=60000')
    _conn.execute('PRAGMA synchronous=NORMAL')
    init_tables(_conn)

    # Safety: only allow scraped.db (path must contain "scraped").
    # Prevents accidental pointing at clean.db, which would corrupt scraper data.
    db_basename = os.path.basename(args.db)
    if 'scraped' not in db_basename and not getattr(args, 'unsafe_any_db', False):
        log.error(
            f"SAFETY CHECK FAILED: DB path '{args.db}' does not look like scraped.db "
            f"(filename must contain 'scraped'). "
            f"Pass --unsafe-any-db to override. Refusing to start."
        )
        _conn.close()
        os.unlink(pid_file)
        sys.exit(1)

    log.info(f"DB: {args.db}")

    if os.path.exists(args.socket):
        os.unlink(args.socket)

    server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server_sock.bind(args.socket)
    server_sock.listen(64)
    server_sock.settimeout(1.0)
    log.info(f"Listening: {args.socket} (PID {os.getpid()})")

    def _on_signal(sig, frame):
        log.info(f"Signal {sig} — shutting down")
        _shutdown.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT,  _on_signal)

    threads = []
    while not _shutdown.is_set():
        try:
            client_sock, _ = server_sock.accept()
            t = threading.Thread(target=_handle_client, args=(client_sock,), daemon=True)
            t.start()
            threads.append(t)
            threads = [t for t in threads if t.is_alive()]
        except socket.timeout:
            continue
        except Exception as e:
            if not _shutdown.is_set():
                log.error(f"Accept error: {e}")

    log.info(f"Shutdown: waiting for {len([t for t in threads if t.is_alive()])} threads")
    for t in threads:
        t.join(timeout=10)

    server_sock.close()
    for path in (args.socket, pid_file):
        try:
            os.unlink(path)
        except Exception:
            pass

    _conn.close()
    log.info("Server stopped.")


if __name__ == '__main__':
    main()
