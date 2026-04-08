import sys
import os
import socket
import signal
import traceback
import logging

# Setup logging to file AND console
log_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'server_output.log')

# Clear old log
if os.path.exists(log_path):
    os.remove(log_path)

# Configure logging to output to BOTH file and console
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(log_path),
        logging.StreamHandler(sys.stdout)  # Also print to console
    ]
)

def kill_port_8000():
    """Kill any process using port 8000"""
    import subprocess
    try:
        # Find PIDs using port 8000
        result = subprocess.run(
            ['netstat', '-ano'],
            capture_output=True, text=True, shell=True
        )
        for line in result.stdout.split('\n'):
            if ':8000' in line and 'LISTENING' in line:
                parts = line.split()
                pid = parts[-1]
                if pid.isdigit():
                    os.kill(int(pid), signal.SIGTERM)
                    logging.info(f"Killed process {pid} using port 8000")
    except Exception as e:
        logging.warning(f"Could not kill processes on port 8000: {e}")

logging.info("=" * 50)
logging.info("Server starting...")
logging.info(f"Python: {sys.version}")
logging.info(f"CWD: {os.getcwd()}")

# Kill anything on port 8000
kill_port_8000()

try:
    sys.path.insert(0, os.path.dirname(__file__))
    logging.info("Path setup done")

    from main import app
    logging.info("Main module imported")

    import uvicorn
    logging.info("Uvicorn imported")

    logging.info("Starting uvicorn on 127.0.0.1:8000")
    config = uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="info")
    server = uvicorn.Server(config)
    server.run()

except Exception as e:
    logging.error(f"FATAL: {e}")
    logging.error(traceback.format_exc())
    traceback.print_exc()
