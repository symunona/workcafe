"""
scrape_v2.py — parallel data scraper orchestrator (v2 scrapers)
Runs scraper_kakao_v2.py and scraper_google_v2.py via ralph_loop.py in parallel.
OSM and Naver use original scrapers (they haven't needed a v2 yet).
"""
import subprocess
import time
import sys
import os
import threading

def read_output(p, provider, log_file):
    for line in iter(p.stdout.readline, ''):
        if not line:
            break
        formatted = f"[{provider}] {line.rstrip()}\n"
        log_file.write(formatted)
        log_file.flush()
        sys.stdout.write(formatted)
        sys.stdout.flush()

def run_all():
    scrapers = [
        ("kakao",  "scraper_kakao_v2.py"),
        ("google", "scraper_google_v2.py"),
        ("osm",    "scraper_osm.py"),
        ("naver",  "scraper_naver.py"),
    ]
    os.makedirs("log", exist_ok=True)
    processes = []

    for provider, script in scrapers:
        log_file = open(f"log/{provider}_v2_loop.log", "a")
        cmd = ["../venv/bin/python", "-u", "ralph_loop.py", script]
        print(f"[{provider}] Starting {script}...")
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, bufsize=1)
        t = threading.Thread(target=read_output, args=(p, provider, log_file), daemon=True)
        t.start()
        processes.append((provider, p, log_file, t, script))
        time.sleep(2)

    print("\nAll scrapers running. Ctrl+C to stop.\n")

    try:
        while True:
            for i, (provider, p, log_file, t, script) in enumerate(processes):
                if p.poll() is not None:
                    print(f"[{provider}] Exited ({p.returncode}). Restarting...")
                    new_p = subprocess.Popen(
                        ["../venv/bin/python", "-u", "ralph_loop.py", script],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, bufsize=1
                    )
                    new_t = threading.Thread(target=read_output, args=(new_p, provider, log_file), daemon=True)
                    new_t.start()
                    processes[i] = (provider, new_p, log_file, new_t, script)
            time.sleep(10)
    except KeyboardInterrupt:
        print("\nStopping all scrapers...")
        for provider, p, log_file, t, _ in processes:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
            log_file.close()
        subprocess.run(["pkill", "-f", "playwright"], stderr=subprocess.DEVNULL)
        subprocess.run(["pkill", "-f", "chromium"], stderr=subprocess.DEVNULL)
        print("Done.")

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    run_all()
