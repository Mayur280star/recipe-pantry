"""Use case API-01: pantry search (docs/06)."""

from __future__ import annotations

from dataclasses import dataclass

from api.container import Container
from api.services.validation import check_cuisine, split_multi
from recipe_core.errors import UpstreamUnavailable, ValidationFailed
from recipe_core.models import PantryPage, PantryQuery
from recipe_core.ranking import PreparedPantry, prepare_pantry


@dataclass(frozen=True)
class PantryResult:
    pantry: PreparedPantry
    staples_assumed: tuple[str, ...]
    page: PantryPage
    sql_run_id: str | None


class PantrySearchService:
    def __init__(self, c: Container):
        self.c = c

    def search(self, ingredients: list[str], *, max_prep_time: int | None = None, max_calories: int | None = None,
               cuisine: str | None = None, min_match_pct: float = 0.0, assume_staples: bool = True,
               limit: int = 20, offset: int = 0) -> PantryResult:
        tokens = split_multi(ingredients, "ingredients", self.c.max_pantry_items)
        if not tokens:
            raise ValidationFailed("ingredients: at least one item is required")
        staples = self.c.staples if assume_staples else frozenset()
        prepared = prepare_pantry(tokens, self.c.canonicalizer, staples)
        if cuisine is not None:
            cuisine = check_cuisine(cuisine)
            self._require_known_cuisine(cuisine)
        page = self.c.index.search(PantryQuery(
            pantry_ids=prepared.pantry_ids, staples=staples, max_prep_time=max_prep_time,
            max_calories=max_calories, cuisine=cuisine, min_match_pct=min_match_pct, limit=limit, offset=offset))
        return PantryResult(prepared, tuple(sorted(staples)), page, self._sql_run_id())

    def _require_known_cuisine(self, cuisine: str) -> None:
        try:
            known = {c.cuisine for c in self.c.catalog.cuisines()}
        except UpstreamUnavailable:
            return                                     # SQL tier down: pantry search must still work
        if cuisine not in known:
            raise ValidationFailed(f"cuisine: unknown cuisine {cuisine!r}")

    def _sql_run_id(self) -> str | None:
        try:
            return self.c.catalog.snapshot().get("run_id")
        except UpstreamUnavailable:
            return None
