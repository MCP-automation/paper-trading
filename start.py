"""
FINAL ROBUST STARTUP - Binance Paper Trading
"""

import os
import sys
import subprocess
import time


def start():
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    PYTHON = os.path.join(BASE_DIR, "venv", "Scripts", "python.exe")
    if not os.path.exists(PYTHON):
        PYTHON = "python"

    print("🗡️ Cleaning port 8000...")
    try:
        out = subprocess.check_output(["netstat", "-ano"], text=True)
        for line in out.split("\n"):
            if ":8000" in line and "LISTENING" in line:
                pid = line.strip().split()[-1]
                print(f"   Killing PID {pid}")
                subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
                time.sleep(1)
    except:
        pass

    print("🚀 Starting server on http://127.0.0.1:8000 ...")

    # Use uvicorn directly
    cmd = [
        PYTHON,
        "-m",
        "uvicorn",
        "backend.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
    ]
    proc = subprocess.Popen(
        cmd, cwd=BASE_DIR, creationflags=subprocess.CREATE_NEW_CONSOLE
    )

    print(f"✅ Server started with PID {proc.pid}")
    print("🔗 Open: http://127.0.0.1:8000")
    print("\nCheck console window for errors!")


if __name__ == "__main__":
    start()
