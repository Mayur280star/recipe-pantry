"""Dependency container: everything a request needs, typed by ports only (docs/09 §6).

The composition root (api/main.py) builds it. `ContainerHolder` rebuilds it when the published snapshot or
rule version changes (checked at most every CACHE_RELOAD_S), so a new ETL publish or a rollback is picked
up without restarting the API. If a rebuild fails the previous container keeps serving (docs/07 §5).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from recipe_core.models import Vocabulary
from recipe_core.normalise import Canonicalizer
from recipe_core.ports import PantryIndex, RecipeCatalog
from recipe_core.ranking import Guards
from recipe_core.rules import RuleSet

log = logging.getLogger("api.container")


@dataclass(frozen=True)
class Container:
    index: PantryIndex
    catalog: RecipeCatalog
    rules: RuleSet
    vocabulary: Vocabulary
    canonicalizer: Canonicalizer
    guards: Guards
    staples: frozenset[str]
    max_pantry_items: int
    version_key: str                     # changes when snapshot/rules change


class ContainerHolder:
    def __init__(self, build: Callable[[], Container], probe_version: Callable[[], str], reload_s: float):
        self._build, self._probe, self._reload_s = build, probe_version, reload_s
        self._lock = threading.Lock()
        self._container: Container | None = None
        self._checked = 0.0
        self.last_error: str | None = None
        self.reloads = 0

    def get(self) -> Container:
        now = time.monotonic()
        if self._container is not None and now - self._checked < self._reload_s:
            return self._container
        with self._lock:
            if self._container is not None and time.monotonic() - self._checked < self._reload_s:
                return self._container
            self._checked = time.monotonic()
            try:
                version = self._probe()
                if self._container is None or version != self._container.version_key:
                    self._container = self._build()
                    self.reloads += 1
                    self.last_error = None
                    log.info("container loaded", extra={"version_key": self._container.version_key})
            except Exception as exc:                          # keep serving the stale copy
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.exception("container reload failed")
                if self._container is None:
                    raise
            return self._container

    @property
    def loaded(self) -> bool:
        return self._container is not None
