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


def kill_gracefully(proc):
    """SIGTERM → wait GRACEFUL_TIMEOUT → SIGKILL."""
    if proc.poll() is not None:
        return
    print(f"  Sending SIGTERM...", flush=True)
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=GRACEFUL_TIMEOUT)
        print(f"  Process exited gracefully.", flush=True)
    except subprocess.TimeoutExpired:
        print(f"  Graceful timeout — sending SIGKILL", flush=True)
        proc.kill()
        proc.wait()


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
        )

        # Each combo takes ~90s; 4 keywords per cell → 360s per cell + headroom
        timeout = iterations * 400
        returncode = None

        try:
            proc.wait(timeout=timeout)
            returncode = proc.returncode
        except subprocess.TimeoutExpired:
            print(f"\n[{_ts()}] Timeout after {timeout}s", flush=True)
            kill_gracefully(proc)
            returncode = proc.returncode
        except KeyboardInterrupt:
            print("\nInterrupted — shutting down scraper...", flush=True)
            kill_gracefully(proc)
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

            print("Cleaning up lingering browser processes...", flush=True)
            try:
                subprocess.run(["pkill", "-f", "playwright"], stderr=subprocess.DEVNULL)
                subprocess.run(["pkill", "-f", "chromium"],   stderr=subprocess.DEVNULL)
            except Exception:
                pass
            print("Ready to retry.", flush=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python ralph_loop.py <scraper_script.py>")
        sys.exit(1)

    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    run_loop(sys.argv[1])
