"""HTTP client for the UI. Client rules from docs/08 §6: 8 s timeout, one retry on 503 after 500 ms,
never retry 422. Returns (data, error_message) so the UI can render error states instead of crashing.
"""

from __future__ import annotations

import os
import time
from typing import Any

import requests

API_URL = os.environ.get("API_URL", "http://localhost:8000").rstrip("/")
TIMEOUT_S = 8
_session = requests.Session()


def get(path: str, params: dict[str, Any] | None = None) -> tuple[Any | None, str | None]:
    clean = {k: v for k, v in (params or {}).items() if v not in (None, "", [])}
    for attempt in (1, 2):
        try:
            r = _session.get(f"{API_URL}{path}", params=clean, timeout=TIMEOUT_S)
        except requests.Timeout:
            return None, "The server took too long to answer. Please retry."
        except requests.ConnectionError:
            return None, f"Cannot reach the API at {API_URL}. Is it running? (make lite-api)"
        if r.status_code == 503 and attempt == 1:
            time.sleep(0.5)
            continue
        if r.ok:
            return r.json(), None
        try:
            err = r.json()["error"]
            msg = err["message"]
        except (ValueError, KeyError, TypeError):
            msg = r.text[:200]
        if r.status_code == 503:
            return None, "Search is temporarily unavailable. Please retry in a moment."
        if r.status_code == 404:
            return None, "Not found — the recipe may have been updated. Reload the list."
        return None, msg
    return None, "Service unavailable."
