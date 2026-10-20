"""Ports (interfaces) the API services depend on (docs/09 §4). Adapters implement them.

Solr / in-memory implement PantryIndex; Trino / DuckDB implement RecipeCatalog, RuleRepository and
VocabularyRepository. Each pair must pass the same contract tests in tests/contract (Liskov).
"""

from __future__ import annotations

from typing import Protocol

from recipe_core.models import (
    CuisineCount,
    CuisinePage,
    CuisineQuery,
    PantryPage,
    PantryQuery,
    RecipeDetail,
    Vocabulary,
)
from recipe_core.rules import RuleSet


class PantryIndex(Protocol):
    def search(self, query: PantryQuery) -> PantryPage: ...

    def ping(self) -> None:
        """Raise UpstreamUnavailable if the index cannot serve."""
        ...

    def run_id(self) -> str | None: ...


class RecipeCatalog(Protocol):
    def by_cuisine(self, query: CuisineQuery) -> CuisinePage: ...

    def detail(self, recipe_id: str) -> RecipeDetail | None: ...

    def cuisines(self) -> list[CuisineCount]: ...

    def ping(self) -> None: ...

    def snapshot(self) -> dict[str, str]:
        """{'run_id': ..., 'rule_version': ..., 'core_version': ...} of the published snapshot."""
        ...

    def run_summary(self) -> dict:
        """ETL run row (etl_runs), DQ gate results (dq_gates) and headline table counts for the live snapshot."""
        ...


class RuleRepository(Protocol):
    def load_all(self) -> RuleSet: ...

    def current_version(self) -> str: ...


class VocabularyRepository(Protocol):
    def load(self) -> Vocabulary: ...
