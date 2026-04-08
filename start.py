"""
Launch the paper trading server as a fully detached Windows process.
This script exits immediately after launching the server.
"""
import subprocess
import sys
import os
import time
import signal

PYTHON = r"C:\Users\Naman\paper-trading\venv\Scripts\python.exe"
BACKEND_DIR = os.path.join(os.path.dirname(__file__), "backend")
LOG_FILE = os.path.join(os.path.dirname(__file__), "server_output.log")
RUN_SCRIPT = os.path.join(BACKEND_DIR, "run_with_logging.py")

# ============================================================
# Kill anything on port 8000
# ============================================================
def kill_port_8000():
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(('127.0.0.1', 8000))
    sock.close()
    if result != 0:
        return  # Port is free
    
    print("Port 8000 is in use. Attempting to kill old server...")
    try:
        netstat = r"C:\Windows\System32\netstat.exe"
        out = subprocess.check_output([netstat, '-ano'], text=True)
        for line in out.split('\n'):
            if ':8000' in line and 'LISTENING' in line:
                pid = int(line.split()[-1])
                os.kill(pid, signal.SIGTERM)
                print(f"  Killed PID {pid}")
                time.sleep(1)
    except Exception as e:
        print(f"  Warning: Could not kill: {e}")
        print("  The new server will try to start anyway...")

kill_port_8000()

# ============================================================
# Clear old log
# ============================================================
if os.path.exists(LOG_FILE):
    os.remove(LOG_FILE)

# ============================================================
# Launch server as fully detached process
# ============================================================
print("Launching paper trading server...")

proc = subprocess.Popen(
    [PYTHON, RUN_SCRIPT],
    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NEW_CONSOLE,
    cwd=BACKEND_DIR
)

print(f"Server launched in new window (PID: {proc.pid})")
print("")
print("Wait ~10 seconds, then open in your browser:")
print("  http://127.0.0.1:8000")
print("")
print(f"Log file: {LOG_FILE}")
