import subprocess
import time
import sys
import os
import threading

def read_output(p, provider, log_file):
    # Read line by line from process stdout
    for line in iter(p.stdout.readline, ''):
        if not line:
            break
        # Strip newline for printing, then add it back
        line = line.rstrip('\n')
        # Format the line
        formatted_line = f"[{provider}] {line}\n"
        
        # Write to log file
        log_file.write(formatted_line)
        log_file.flush()
        
        # Print to terminal
        sys.stdout.write(formatted_line)
        sys.stdout.flush()

def run_all_scrapers():
    scrapers = ["scraper_naver.py", "scraper_kakao.py", "scraper_google.py", "scraper_osm.py"]
    processes = []
    
    print(f"Starting parallel scraping with: {', '.join(scrapers)}")
    
    # Ensure logs directory exists
    os.makedirs("logs", exist_ok=True)
    
    for scraper in scrapers:
        provider = scraper.split('_')[1].split('.')[0]
        log_file = open(f"logs/{provider}_loop.log", "a")
        
        cmd = ["../venv/bin/python", "-u", "ralph_loop.py", scraper]
        
        print(f"[{provider}] Starting loop process...")
        p = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        
        # Start a thread to read output
        t = threading.Thread(target=read_output, args=(p, provider, log_file), daemon=True)
        t.start()
        
        processes.append((provider, p, log_file, t))
        
        # Stagger startups
        time.sleep(2)
        
    try:
        print("\nAll scrapers running in parallel. Logs are in the logs/ directory.")
        print("Press Ctrl+C to stop all scrapers.\n")
        
        while True:
            for i, (provider, p, log_file, t) in enumerate(processes):
                if p.poll() is not None:
                    print(f"[{provider}] Process exited with code {p.returncode}. Restarting...")
                    cmd = ["../venv/bin/python", "-u", "ralph_loop.py", f"scraper_{provider}.py"]
                    new_p = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1
                    )
                    new_t = threading.Thread(target=read_output, args=(new_p, provider, log_file), daemon=True)
                    new_t.start()
                    processes[i] = (provider, new_p, log_file, new_t)
            
            time.sleep(10)
            
    except KeyboardInterrupt:
        print("\nStopping all scrapers...")
        for provider, p, log_file, t in processes:
            print(f"[{provider}] Terminating...")
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
            log_file.close()
        
        print("Cleaning up zombie browser processes...")
        try:
            subprocess.run(["pkill", "-f", "playwright"], stderr=subprocess.DEVNULL)
            subprocess.run(["pkill", "-f", "chrome"], stderr=subprocess.DEVNULL)
            subprocess.run(["pkill", "-f", "chromium"], stderr=subprocess.DEVNULL)
        except Exception:
            pass
            
        print("All scrapers stopped.")

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    run_all_scrapers()
