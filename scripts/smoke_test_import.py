"""Simple smoke test to verify importing the main Streamlit pages doesn't crash.

This script does not launch Streamlit; it only imports the module to catch
syntax/import-time errors.
"""

import importlib

MODULE = "pages.eod_workflow"

try:
    importlib.import_module(MODULE)
    print(f"OK: imported {MODULE}")
except Exception as e:
    print(f"ERROR importing {MODULE}: {e}")
    raise
