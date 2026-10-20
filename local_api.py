"""Run the FastAPI app in a daemon thread. Module-level code runs once per process (Streamlit reruns the page script,
not its imports)."""

import os
import threading
import time
import urllib.request

import uvicorn

PORT = int(os.environ.get("LOCAL_API_PORT", "8765"))


def _serve() -> None:
    from api.main import app

    uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning")).run()


threading.Thread(target=_serve, name="recipe-api", daemon=True).start()


_warm = False
# First calls on a cold container (imports, DuckDB, file cache) can exceed the UI's 8 s request timeout: make them here.
WARMUP = ("/api/v1/system/summary", "/api/v1/cuisines", "/api/v1/cuisine-search?cuisine=italian",
          "/api/v1/pantry-search?ingredients=egg&ingredients=milk&ingredients=flour")


def wait_ready(timeout_s: float = 300) -> None:
    global _warm
    if _warm:
        return
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health/ready", timeout=2).status == 200:
                break
        except Exception:
            time.sleep(0.5)
    else:
        raise RuntimeError("recipe API did not become ready")
    for path in WARMUP:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=120).read()
    _warm = True
