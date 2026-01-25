import os
import sys
import multiprocessing
import logging

# 1. Add the project root to sys.path so we can import 'src'
#    When running from the source: __file__ is installer/backend_entry.py, so root is ..
#    When running frozen (PyInstaller): sys._MEIPASS is the temp dir, and we are at the top level there.
if getattr(sys, 'frozen', False):
    # Running in a PyInstaller bundle
    base_dir = sys._MEIPASS
else:
    # Running as a script
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

sys.path.insert(0, base_dir)

from src.api.main import app
import uvicorn

if __name__ == "__main__":
    # PyInstaller needs this for multiprocessing to work (if used)
    multiprocessing.freeze_support()
    
    # Get port from args or default to 8000
    # We can also parse args if we need more flexibility
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args, unknown = parser.parse_known_args()

    uvicorn.run(app, host=args.host, port=args.port)
