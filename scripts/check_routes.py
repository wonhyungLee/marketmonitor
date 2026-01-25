import sys
from pathlib import Path
sys.path.append(str(Path.cwd()))
from app.main import app

print("--- Registered Routes ---")
for route in app.routes:
    print(f"Path: {route.path} | Name: {route.name}")
