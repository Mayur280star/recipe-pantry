"""Frozen DTOs shared by services, adapters and the ETL. Field names follow docs/04 (TBL-nn, IDX-01)."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property

# ---------------------------------------------------------------------------------------------
# Pantry search (API-01, docs/06)
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RecipeCard:
    """Summary fields stored in Solr so pantry search works when the SQL tier is down (docs/03 §8)."""

    recipe_id: str
    recipe_name: str
    cuisine: str | None = None
    prep_time_min: int | None = None
    cook_time_min: int | None = None
    total_time_min: int | None = None
    calories: int | None = None


@dataclass(frozen=True)
class RecipeCandidate:
    """What the ranker needs about one recipe: required (R) and optional (O) canonical ids."""

    card: RecipeCard
    ingredient_ids: frozenset[str]
    optional_ingredient_ids: frozenset[str] = frozenset()
    run_id: str | None = None

    @property
    def recipe_id(self) -> str:
        return self.card.recipe_id

    @property
    def ingredient_count(self) -> int:
        return len(self.ingredient_ids)


@dataclass(frozen=True)
class PantryQuery:
    pantry_ids: frozenset[str]              # P: resolved, non-staple
    staples: frozenset[str]                 # S
    max_prep_time: int | None = None
    max_calories: int | None = None
    cuisine: str | None = None
    min_match_pct: float = 0.0
    limit: int = 20
    offset: int = 0


@dataclass(frozen=True)
class ScoredRecipe:
    card: RecipeCard
    ingredient_count: int
    match_percentage: float                  # rounded to 2 dp for display
    matched_ingredients: tuple[str, ...]
    missing_ingredients: tuple[str, ...]
    optional_missing: tuple[str, ...]

    @property
    def recipe_id(self) -> str:
        return self.card.recipe_id


@dataclass(frozen=True)
class PantryPage:
    total_matches: int
    recipes: tuple[ScoredRecipe, ...]
    run_id: str | None = None


# ---------------------------------------------------------------------------------------------
# Catalog (API-02, API-03, API-05)
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CuisineQuery:
    cuisine: str
    max_prep_time: int | None = None
    max_calories: int | None = None
    limit: int = 20
    offset: int = 0


@dataclass(frozen=True)
class CuisineRecipe:
    card: RecipeCard
    ingredient_count: int


@dataclass(frozen=True)
class CuisinePage:
    cuisine: str
    recipes: tuple[CuisineRecipe, ...]
    limit: int
    offset: int
    next_offset: int | None


@dataclass(frozen=True)
class IngredientLine:
    line_no: int
    raw_text: str
    ingredient_id: str | None
    ingredient_name: str
    quantity: float | None
    unit: str
    is_optional: bool


@dataclass(frozen=True)
class RecipeDetail:
    card: RecipeCard
    cuisine_source: str | None
    servings: int | None
    ingredients: tuple[IngredientLine, ...]
    steps: tuple[tuple[int, str], ...]
    utensils: tuple[str, ...]


@dataclass(frozen=True)
class CuisineCount:
    cuisine: str
    recipe_count: int

    @property
    def is_unspecified(self) -> bool:
        return self.cuisine == "unspecified"


# ---------------------------------------------------------------------------------------------
# Vocabulary (API-06, normaliser cache)
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class VocabEntry:
    ingredient_id: str
    canonical_name: str
    recipe_count: int = 0
    category: str = "other"


@dataclass(frozen=True)
class Vocabulary:
    entries: tuple[VocabEntry, ...]
    alias_to_id: dict[str, str] = field(default_factory=dict, hash=False, compare=False)

    @cached_property
    def id_to_name(self) -> dict[str, str]:
        return {e.ingredient_id: e.canonical_name for e in self.entries}

    @cached_property
    def id_to_category(self) -> dict[str, str]:
        return {e.ingredient_id: e.category for e in self.entries}

    def suggest(self, prefix: str, limit: int = 10) -> list[VocabEntry]:
        """Prefix match on canonical name or any word of it, ranked by recipe_count desc (API-06)."""
        p = prefix.strip().lower()
        if not p:
            return []
        hits = [e for e in self.entries
                if e.canonical_name.startswith(p) or any(w.startswith(p) for w in e.canonical_name.split())]
        hits.sort(key=lambda e: (-e.recipe_count, e.canonical_name))
        return hits[:limit]
