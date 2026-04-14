import subprocess
import time
import sys
import os
import signal

GRACEFUL_TIMEOUT = 15  # seconds to wait for SIGTERM before SIGKILL


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


def run_loop(scraper_script):
    iterations = 100
    consecutive_errors = 0

    while True:
        print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Running {scraper_script} for {iterations} iterations...", flush=True)

        proc = subprocess.Popen(
            ["../venv/bin/python", scraper_script, "--max-steps", str(iterations)]
        )

        timeout = iterations * 60
        returncode = None

        try:
            proc.wait(timeout=timeout)
            returncode = proc.returncode
        except subprocess.TimeoutExpired:
            print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Timeout after {timeout}s", flush=True)
            kill_gracefully(proc)
            returncode = proc.returncode
        except KeyboardInterrupt:
            print("\nInterrupted — shutting down scraper...", flush=True)
            kill_gracefully(proc)
            break

        if returncode == 0:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Success!", flush=True)
            iterations += 100
            consecutive_errors = 0
            print(f"Increasing iterations to {iterations}.", flush=True)
        elif returncode == 42:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Scraper reports all blocks completed. Shutting down.", flush=True)
            sys.exit(0)
        else:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Error: exit code {returncode}", flush=True)
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
