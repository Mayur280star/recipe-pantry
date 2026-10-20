"""Streamlit Community Cloud entry point (lite profile in one process).

The FastAPI app (in-memory pantry index + DuckDB over the bundled Parquet snapshot) starts once per process in a
background thread on 127.0.0.1; the unchanged Streamlit UI then talks to it over HTTP exactly as it does locally.
"""

import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = os.environ.setdefault("LOCAL_API_PORT", "8765")
os.environ.setdefault("PROFILE", "lite")
os.environ.setdefault("SNAPSHOT_ROOT", str(ROOT / "data" / "warehouse"))
os.environ.setdefault("API_URL", f"http://127.0.0.1:{PORT}")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import local_api  # noqa: E402  (imported once per process: starts the API thread)

local_api.wait_ready()
runpy.run_path(str(ROOT / "ui" / "app.py"), run_name="__main__")
