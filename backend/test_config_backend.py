#!/usr/bin/env python3
import os
import json

# Simulate main.py path calculation
main_py_path = os.path.join(os.getcwd(), "main.py")  # Assuming we're in backend/
backend_dir = os.path.dirname(main_py_path)
project_root = os.path.dirname(backend_dir)
config_path = os.path.join(project_root, "config.json")

if os.path.exists(config_path):
    with open(config_path, "r") as f:
        config = json.load(f)
else:
    pass
