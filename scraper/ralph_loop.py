import subprocess
import time
import sys
import os

def run_loop(scraper_script):
    iterations = 100
    consecutive_errors = 0
    
    while True:
        print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Running {scraper_script} for {iterations} iterations...", flush=True)
        try:
            # We run the scraper script using the virtualenv python
            result = subprocess.run(
                ["../venv/bin/python", scraper_script, "--max-steps", str(iterations)],
                check=True,
                # Setting a generous timeout to catch complete hangs that escape the internal watchdogs
                timeout=iterations * 60 
            )
            print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Success! No errors encountered.")
            iterations += 100
            consecutive_errors = 0
            print(f"Increasing iterations to {iterations}.")
        except subprocess.TimeoutExpired:
            print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Error: Script timed out completely!")
            consecutive_errors += 1
            iterations = 100
            print("Resetting to 100 iterations.")
        except subprocess.CalledProcessError as e:
            print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Error: Script failed with exit code {e.returncode}")
            consecutive_errors += 1
            iterations = 100
            print("Resetting to 100 iterations.")
        except KeyboardInterrupt:
            print("\nLoop interrupted by user. Exiting.")
            break
            
        if consecutive_errors > 0:
            # Try to fix the environment before retrying
            print("Attempting to fix environment/network issues...")
            
            # Backoff linearly with consecutive errors
            sleep_time = 10 * consecutive_errors
            print(f"Sleeping for {sleep_time} seconds to let rate limits or network issues clear...")
            time.sleep(sleep_time)
            
            # Clear any lingering scraper/browser processes that may be holding the DB lock
            print("Cleaning up zombie scraper and browser processes...")
            try:
                subprocess.run(["pkill", "-f", scraper_script], stderr=subprocess.DEVNULL)
                subprocess.run(["pkill", "-f", "playwright"], stderr=subprocess.DEVNULL)
                subprocess.run(["pkill", "-f", "chrome"], stderr=subprocess.DEVNULL)
                subprocess.run(["pkill", "-f", "chromium"], stderr=subprocess.DEVNULL)
            except Exception:
                pass
            
            print("Environment reset. Ready to try again.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python ralph_loop.py <scraper_script.py>")
        print("Example: python ralph_loop.py scraper_naver.py")
        sys.exit(1)
    
    # Change to the directory of the script to ensure paths (like ../venv) work
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    run_loop(sys.argv[1])
