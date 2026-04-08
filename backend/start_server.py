"""Launch script that starts the server as a truly independent Windows process"""
import os
import sys
import subprocess

venv_python = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'venv', 'Scripts', 'python.exe')
main_py = os.path.join(os.path.dirname(__file__), 'main.py')

# Use CREATE_NEW_PROCESS_GROUP + DETACHED_PROCESS to fully detach
creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW

proc = subprocess.Popen(
    [venv_python, main_py],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    cwd=os.path.dirname(__file__),
    creationflags=creation_flags
)
