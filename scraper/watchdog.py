#!/usr/bin/env python3
"""
watchdog.py — scraper health monitor

Runs every 30 min (via systemd timer). Checks image scraper log recency.
If last log > STALE_MINUTES, restarts the service and increments auto_restarts.
Manual restarts (PID changes we didn't cause) reset the counter.

Status written to ../data/watchdog-status.json → served at /api/watchdog-status.

Usage:
    python watchdog.py                       # health check (called by timer)
    python watchdog.py --reset kakao-images  # reset counter after manual restart
    python watchdog.py --stale-minutes 15    # override stale threshold
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
STATUS_FILE = (SCRIPT_DIR / '..' / 'data' / 'watchdog-status.json').resolve()

DEFAULT_STALE_MINUTES = int(os.environ.get('WATCHDOG_STALE_MINUTES', '30'))

SERVICES: dict[str, str] = {
    'kakao-images':  'workcafe-kakao-images',
    'naver-images':  'workcafe-naver-images',
    'google-images': 'workcafe-google-images',
}


def run(cmd: list[str]) -> tuple[int, str]:
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def systemctl_props(unit: str, props: list[str]) -> dict[str, str]:
    _, out = run(['systemctl', '--user', 'show', unit, '--property=' + ','.join(props)])
    result: dict[str, str] = {}
    for line in out.splitlines():
        if '=' in line:
            k, _, v = line.partition('=')
            result[k.strip()] = v.strip()
    return result


def last_journal_time(unit: str) -> datetime | None:
    _, out = run(['journalctl', '--user', '-u', unit, '-n1',
                  '--output=short-iso', '--no-pager'])
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith('--'):
            continue
        try:
            ts_str = line.split()[0]
            # short-iso: "2026-04-22T03:07:28+0200" — fix missing colon in offset
            if len(ts_str) > 5 and ts_str[-5] in ('+', '-') and ':' not in ts_str[-5:]:
                ts_str = ts_str[:-2] + ':' + ts_str[-2:]
            return datetime.fromisoformat(ts_str)
        except Exception:
            continue
    return None


def parse_systemd_ts(ts: str) -> datetime | None:
    if not ts or ts in ('n/a', ''):
        return None
    try:
        rc, out = run(['date', '--date', ts, '--iso-8601=seconds'])
        if rc == 0 and out.strip():
            return datetime.fromisoformat(out.strip())
    except Exception:
        pass
    return None


def load_status() -> dict:
    try:
        return json.loads(STATUS_FILE.read_text())
    except Exception:
        return {'services': {}}


def save_status(data: dict) -> None:
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data['updated_at'] = datetime.now(timezone.utc).isoformat()
    STATUS_FILE.write_text(json.dumps(data, indent=2, default=str))


def check_service(name: str, unit: str, prev: dict, stale_minutes: int) -> dict:
    now = datetime.now(timezone.utc)

    props = systemctl_props(unit, ['ActiveState', 'MainPID', 'ActiveEnterTimestamp'])
    active_state = props.get('ActiveState', 'unknown')
    is_active = active_state == 'active'
    try:
        current_pid = int(props.get('MainPID', '0'))
    except ValueError:
        current_pid = 0

    last_seen_pid = prev.get('last_seen_pid', 0)
    expecting_pid_change = prev.get('_expecting_pid_change', False)
    auto_restarts = prev.get('auto_restarts', 0)

    # Detect external restart (manual or systemd on-failure): PID changed unexpectedly
    if current_pid and last_seen_pid and current_pid != last_seen_pid:
        if expecting_pid_change:
            # This is the PID change from our last restart — expected
            expecting_pid_change = False
        else:
            # Someone else restarted it → reset counter
            print(f'[watchdog] {name}: external restart detected (pid {last_seen_pid}→{current_pid}), resetting counter')
            auto_restarts = 0

    last_log_ts = last_journal_time(unit)
    last_log_age_s = int((now - last_log_ts.astimezone(timezone.utc)).total_seconds()) if last_log_ts else None

    stale = is_active and last_log_age_s is not None and last_log_age_s > stale_minutes * 60
    restarted = False

    if stale:
        age_min = last_log_age_s // 60
        print(f'[watchdog] {name}: STALE ({age_min}m silent) — restarting {unit}')
        rc, out = run(['systemctl', '--user', 'restart', unit])
        if rc == 0:
            auto_restarts += 1
            expecting_pid_change = True
            restarted = True
            print(f'[watchdog] {name}: restarted OK (auto_restarts now {auto_restarts})')
        else:
            print(f'[watchdog] {name}: restart FAILED: {out}', file=sys.stderr)

    return {
        'unit': unit,
        'active': is_active,
        'active_state': active_state,
        'pid': current_pid,
        'last_seen_pid': current_pid,
        'last_log_at': last_log_ts.isoformat() if last_log_ts else None,
        'last_log_age_s': last_log_age_s,
        'stale': stale,
        'healthy': is_active and not stale,
        'auto_restarts': auto_restarts,
        'last_watchdog_restart': now.isoformat() if restarted else prev.get('last_watchdog_restart'),
        '_expecting_pid_change': expecting_pid_change,
    }


def reset_counter(name: str) -> None:
    status = load_status()
    svc = status.setdefault('services', {}).setdefault(name, {})
    svc['auto_restarts'] = 0
    svc['_expecting_pid_change'] = False
    save_status(status)
    print(f'[watchdog] reset auto_restarts for {name}')


def main() -> None:
    parser = argparse.ArgumentParser(description='Workcafe scraper watchdog')
    parser.add_argument('--stale-minutes', type=int, default=DEFAULT_STALE_MINUTES,
                        help=f'Minutes of log silence before restart (default: {DEFAULT_STALE_MINUTES})')
    parser.add_argument('--reset', metavar='SERVICE',
                        help='Reset restart counter for a service (use after manual restart)')
    args = parser.parse_args()

    if args.reset:
        if args.reset not in SERVICES:
            print(f'Unknown service: {args.reset!r}. Valid: {list(SERVICES)}', file=sys.stderr)
            sys.exit(1)
        reset_counter(args.reset)
        return

    status = load_status()
    status.setdefault('services', {})
    status['stale_threshold_minutes'] = args.stale_minutes

    for name, unit in SERVICES.items():
        try:
            prev = status['services'].get(name, {})
            entry = check_service(name, unit, prev, args.stale_minutes)
            status['services'][name] = entry
        except Exception as e:
            print(f'[watchdog] ERROR checking {name}: {e}', file=sys.stderr)
            status['services'].setdefault(name, {})['error'] = str(e)

    save_status(status)
    print(f'[watchdog] done — status written to {STATUS_FILE}')


if __name__ == '__main__':
    main()
