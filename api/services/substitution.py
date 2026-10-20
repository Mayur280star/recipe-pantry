"""Use case API-04: deterministic substitution lookup (docs/07 §4). Served from memory — no DB call."""

from __future__ import annotations

from dataclasses import dataclass

from api.container import Container
from api.services.validation import clean_token, split_multi
from recipe_core.errors import ValidationFailed
from recipe_core.normalise import CANONICAL_UNITS
from recipe_core.rules import CONTEXTS, DIET_TAGS, SubstituteHit, lookup


@dataclass(frozen=True)
class SubstitutionResult:
    input: str
    ingredient_id: str | None
    canonical_name: str | None
    rule_version: str
    hits: tuple[SubstituteHit, ...]
    names: dict[str, str]


class SubstitutionService:
    def __init__(self, c: Container):
        self.c = c

    def substitute(self, ingredient: str, pantry: list[str] | None = None, diet: list[str] | None = None,
                   context: str | None = None, amount: float | None = None,
                   unit: str | None = None) -> SubstitutionResult:
        text = clean_token(ingredient, "ingredient")
        diet_set = frozenset(d.strip().lower() for d in split_multi(diet, "diet", 7))
        if not diet_set <= DIET_TAGS:
            raise ValidationFailed(f"diet: allowed values are {sorted(DIET_TAGS)}")
        if context is not None and (context not in CONTEXTS or context == "any"):
            raise ValidationFailed("context: must be baking, cooking or raw")
        if (amount is None) != (unit is None):
            raise ValidationFailed("amount and unit must be given together")
        if unit is not None and unit not in CANONICAL_UNITS:
            raise ValidationFailed(f"unit: must be one of {list(CANONICAL_UNITS)}")

        canon = self.c.canonicalizer
        iid, name = canon.resolve(text)
        pantry_ids = frozenset(i for t in split_multi(pantry, "pantry", self.c.max_pantry_items)
                               if (i := canon.resolve(t)[0]))
        hits = () if iid is None else tuple(lookup(self.c.rules, iid, pantry_ids, diet_set, context, amount, unit))
        return SubstitutionResult(text, iid, name if iid else None, self.c.rules.version, hits,
                                  self.c.vocabulary.id_to_name)
