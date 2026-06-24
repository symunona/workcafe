import subprocess
import time
import sys
import os
import signal
import itertools

GRACEFUL_TIMEOUT = 15  # seconds to wait for SIGTERM before SIGKILL

# Active scrape regions come from data/regions.json (the global "active" list,
# read via utils). Each provider scraper round-robins across them, so the
# per-provider services (kakao/google/naver/osm) each cover EVERY active region,
# all running in parallel. Edit the `active` list to control what gets scraped.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
try:
    from utils import ACTIVE_REGIONS
except Exception:
    ACTIVE_REGIONS = ["seoul"]


def _kill_group(pgid):
    """SIGKILL a whole process group (the scraper + its browser children only).
    Scoped to one scraper's own group so parallel sibling scrapers' browsers
    are never touched — unlike a system-wide `pkill chromium`."""
    if pgid is None:
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def kill_gracefully(proc, pgid=None):
    """SIGTERM the group → wait GRACEFUL_TIMEOUT → SIGKILL the group."""
    if proc.poll() is not None:
        _kill_group(pgid)  # reap any orphaned browser children
        return
    print(f"  Sending SIGTERM...", flush=True)
    try:
        if pgid is not None:
            os.killpg(pgid, signal.SIGTERM)
        else:
            proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=GRACEFUL_TIMEOUT)
        print(f"  Process exited gracefully.", flush=True)
    except subprocess.TimeoutExpired:
        print(f"  Graceful timeout — sending SIGKILL", flush=True)
        _kill_group(pgid) if pgid is not None else proc.kill()
        proc.wait()
    _kill_group(pgid)


def _ts():
    return time.strftime('%Y-%m-%d %H:%M:%S')


def run_loop(scraper_script):
    regions = list(ACTIVE_REGIONS) or ["seoul"]
    print(f"[{_ts()}] {scraper_script} — active regions: {regions}", flush=True)
    region_cycle = itertools.cycle(regions)
    done = set()             # regions that reported all-blocks-complete this run
    iterations = 500
    consecutive_errors = 0

    while True:
        # Pick the next region that isn't fully scraped yet.
        region = None
        for _ in range(len(regions)):
            r = next(region_cycle)
            if r not in done:
                region = r
                break
        if region is None:
            print(f"[{_ts()}] All active regions completed. Shutting down.", flush=True)
            sys.exit(0)

        print(f"\n[{_ts()}] Running {scraper_script} [{region}] for {iterations} iterations...", flush=True)

        env = {**os.environ, "WORKCAFE_REGION": region}
        proc = subprocess.Popen(
            ["../venv/bin/python", scraper_script, "--max-steps", str(iterations)],
            env=env,
            start_new_session=True,  # own process group → scoped browser cleanup
        )
        try:
            pgid = os.getpgid(proc.pid)
        except Exception:
            pgid = None

        # Each combo takes ~90s; 4 keywords per cell → 360s per cell + headroom
        timeout = iterations * 400
        returncode = None

        try:
            proc.wait(timeout=timeout)
            returncode = proc.returncode
        except subprocess.TimeoutExpired:
            print(f"\n[{_ts()}] Timeout after {timeout}s", flush=True)
            kill_gracefully(proc, pgid)
            returncode = proc.returncode
        except KeyboardInterrupt:
            print("\nInterrupted — shutting down scraper...", flush=True)
            kill_gracefully(proc, pgid)
            break

        if returncode == 0:
            print(f"[{_ts()}] [{region}] success.", flush=True)
            iterations = min(iterations + 100, 2000)
            consecutive_errors = 0
        elif returncode == 42:
            # This region reports all blocks complete — drop it from the rotation
            # and keep scraping the others (do NOT exit the whole loop).
            print(f"[{_ts()}] [{region}] all blocks completed — moving to next region.", flush=True)
            done.add(region)
            iterations = 500
            continue
        else:
            print(f"[{_ts()}] [{region}] error: exit code {returncode}", flush=True)
            consecutive_errors += 1
            iterations = 100
            print("Resetting to 100 iterations.", flush=True)

        if consecutive_errors > 0:
            sleep_time = 10 * consecutive_errors
            print(f"Sleeping {sleep_time}s before retry...", flush=True)
            time.sleep(sleep_time)

            print("Cleaning up this scraper's lingering browser children...", flush=True)
            _kill_group(pgid)  # only THIS scraper's group — never sibling providers
            print("Ready to retry.", flush=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python ralph_loop.py <scraper_script.py>")
        sys.exit(1)

    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    run_loop(sys.argv[1])
