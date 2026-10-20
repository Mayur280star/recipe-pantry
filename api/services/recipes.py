"""Use cases API-02 (cuisine search), API-03 (detail), API-05 (cuisines), API-06 (suggest)."""

from __future__ import annotations

from api.container import Container
from api.services.validation import check_cuisine, check_recipe_id
from recipe_core.errors import NotFound, ValidationFailed
from recipe_core.models import CuisineCount, CuisinePage, CuisineQuery, RecipeDetail, VocabEntry


class RecipeService:
    def __init__(self, c: Container):
        self.c = c

    def by_cuisine(self, cuisine: str, max_prep_time: int | None, max_calories: int | None, limit: int,
                   offset: int) -> CuisinePage:
        cuisine = check_cuisine(cuisine)
        if cuisine not in {c.cuisine for c in self.c.catalog.cuisines()}:
            raise ValidationFailed(f"cuisine: unknown cuisine {cuisine!r}")
        return self.c.catalog.by_cuisine(CuisineQuery(cuisine, max_prep_time, max_calories, limit, offset))

    def detail(self, recipe_id: str) -> RecipeDetail:
        d = self.c.catalog.detail(check_recipe_id(recipe_id))
        if d is None:
            raise NotFound(f"recipe {recipe_id} not found")
        return d

    def cuisines(self) -> list[CuisineCount]:
        items = self.c.catalog.cuisines()
        return sorted(items, key=lambda c: (c.is_unspecified, -c.recipe_count, c.cuisine))

    def suggest(self, q: str, limit: int) -> list[VocabEntry]:
        q = q.strip()
        if not 1 <= len(q) <= 32:
            raise ValidationFailed("q: must be 1-32 characters")
        return self.c.vocabulary.suggest(q, limit)

    def snapshot(self) -> dict[str, str]:
        return self.c.catalog.snapshot()

    def run_summary(self) -> dict:
        return self.c.catalog.run_summary()
