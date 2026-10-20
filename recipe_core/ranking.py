"""Pantry-match scoring (docs/06 §2). Fixes F-001, F-024.

    matched   = R ∩ E        where E = P ∪ S   (pantry plus staples)
    missing   = R \\ E
    match_pct = #matched / #R × 100
    optional_missing = O \\ E                  (reported, never penalised)

Ordering is a total order: match_pct desc, #matched desc, recipe_id asc. The Solr adapter reproduces the
same function inside Solr; tests/contract checks both give identical pages.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from recipe_core.errors import EmptyPantry, OnlyStaples
from recipe_core.models import RecipeCandidate, ScoredRecipe
from recipe_core.normalise import Canonicalizer

DEFAULT_STAPLES: frozenset[str] = frozenset({"salt", "water", "black_pepper"})


@dataclass(frozen=True)
class Guards:
    """Eligibility guards (docs/06 §2). min_matches is capped at #P at scoring time."""

    min_recipe_size: int = 3
    min_matches: int = 2
    min_match_pct: float = 0.0


@dataclass(frozen=True)
class PreparedPantry:
    resolved: tuple[tuple[str, str], ...]      # (user input, ingredient_id), staples included
    pantry_ids: frozenset[str]                 # P: resolved minus staples
    unrecognised: tuple[str, ...]


def prepare_pantry(tokens: Iterable[str], canonicalizer: Canonicalizer,
                   staples: frozenset[str] = DEFAULT_STAPLES) -> PreparedPantry:
    """Resolve raw pantry tokens to canonical ids. Raises EmptyPantry / OnlyStaples (-> 422)."""
    resolved: list[tuple[str, str]] = []
    seen: set[str] = set()
    unrecognised: list[str] = []
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        iid, _ = canonicalizer.resolve(token)
        if iid is None:
            if token not in unrecognised:
                unrecognised.append(token)
        elif iid not in seen:
            seen.add(iid)
            resolved.append((token, iid))
    if not resolved:
        raise EmptyPantry("no recognised pantry ingredients")
    pantry = frozenset(seen - staples)
    if not pantry:
        raise OnlyStaples("add at least one non-staple ingredient")
    return PreparedPantry(tuple(resolved), pantry, tuple(unrecognised))


class MatchScorer(Protocol):
    def score(self, pantry: frozenset[str], staples: frozenset[str],
              recipe: RecipeCandidate) -> ScoredRecipe | None: ...


class DefaultMatchScorer:
    """Strict match percentage with eligibility guards. Returns None for ineligible recipes."""

    def __init__(self, guards: Guards | None = None):
        self.guards = guards or Guards()

    def score(self, pantry: frozenset[str], staples: frozenset[str],
              recipe: RecipeCandidate) -> ScoredRecipe | None:
        required = recipe.ingredient_ids
        n_required = len(required)
        if n_required == 0 or n_required < self.guards.min_recipe_size:
            return None
        if len(required & pantry) < min(self.guards.min_matches, len(pantry)):
            return None
        effective = pantry | staples
        matched = required & effective
        pct = len(matched) / n_required * 100
        if pct < self.guards.min_match_pct:
            return None
        return ScoredRecipe(
            card=recipe.card,
            ingredient_count=n_required,
            match_percentage=round(pct, 2),
            matched_ingredients=tuple(sorted(matched)),
            missing_ingredients=tuple(sorted(required - effective)),
            optional_missing=tuple(sorted(recipe.optional_ingredient_ids - effective)),
        )


def sort_key(s: ScoredRecipe) -> tuple[float, int, str]:
    """Total order: exact pct desc, #matched desc, recipe_id asc (stable pagination)."""
    exact = len(s.matched_ingredients) / s.ingredient_count
    return (-exact, -len(s.matched_ingredients), s.recipe_id)


def rank(pantry: frozenset[str], staples: frozenset[str], recipes: Iterable[RecipeCandidate],
         scorer: MatchScorer | None = None) -> list[ScoredRecipe]:
    """Brute-force reference ranking. Used by the lite index and as the parity oracle for Solr."""
    scorer = scorer or DefaultMatchScorer()
    scored = [s for r in recipes if (s := scorer.score(pantry, staples, r)) is not None]
    scored.sort(key=sort_key)
    return scored
