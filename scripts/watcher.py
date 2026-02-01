import time
import sys
import os
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
TRIGGER_FILE = BASE_DIR / "trigger_job"
RUN_SCRIPT = BASE_DIR / "scripts" / "run_daily.py"

def main():
    print(f"Watcher started. Monitoring {TRIGGER_FILE}")
    while True:
        if TRIGGER_FILE.exists():
            print(f"Trigger detected at {time.ctime()}")
            try:
                # Remove file immediately to debounce multiple triggers if file system allows
                # But simple removal is fine. If multiple requests touch it, it stays 'modified'.
                # Just removing it clears the flag.
                try:
                    TRIGGER_FILE.unlink()
                except FileNotFoundError:
                    pass # Race condition, handled by next loop or ignored

                # Run the daily job
                env = os.environ.copy()
                env["PYTHONPATH"] = str(BASE_DIR)
                
                result = subprocess.run(
                    [sys.executable, str(RUN_SCRIPT)],
                    cwd=str(BASE_DIR),
                    env=env,
                    capture_output=True,
                    text=True
                )
                
                if result.returncode != 0:
                    print(f"Job failed: {result.stderr}", file=sys.stderr)
                else:
                    print(f"Job finished successfully.")
                    if result.stdout:
                        print(f"STDOUT:\n{result.stdout}")
                    if result.stderr:
                        print(f"STDERR:\n{result.stderr}")
                    
            except Exception as e:
                print(f"Error running job: {e}", file=sys.stderr)
        
        time.sleep(1)

if __name__ == "__main__":
    main()

