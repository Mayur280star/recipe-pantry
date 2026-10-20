"""Shared, pure-Python core used unchanged by the Spark ETL and the FastAPI service (docs/09 §3).

Rules: no network, no file I/O, no framework or driver imports, no randomness, no wall clock.
Enforced by `.importlinter`.
"""

__version__ = "0.1.0"
core_version = __version__
