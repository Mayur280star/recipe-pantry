"""HTTP layer for API-01…06. Thin: translate query params <-> DTOs; no business logic (docs/09 §2)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from api.routers.deps import pantry_service, recipe_service, substitution_service
from api.schemas.models import (
    CuisineOut,
    CuisineSearchResponse,
    CuisinesResponse,
    IngredientOut,
    IngredientRef,
    PantryEcho,
    PantryRecipe,
    PantrySearchResponse,
    RecipeCardOut,
    RecipeDetailResponse,
    RecipeHeader,
    ResolvedToken,
    Snapshot,
    StepOut,
    SubstituteOut,
    SubstituteResponse,
    SuggestedAmount,
    Suggestion,
    SuggestResponse,
)
from api.services.pantry import PantrySearchService
from api.services.recipes import RecipeService
from api.services.substitution import SubstitutionService
from recipe_core.models import RecipeCard
from recipe_core.rules import DISCLAIMER

router = APIRouter(prefix="/api/v1")

Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=1000)]
Minutes = Annotated[int | None, Query(ge=1, le=1440)]
Calories = Annotated[int | None, Query(ge=1, le=10000)]
CONFIDENCE_LABEL = {1: "may work", 2: "good", 3: "strong"}


def _card(c: RecipeCard, ingredient_count: int) -> dict:
    return {"recipe_id": c.recipe_id, "recipe_name": c.recipe_name, "cuisine": c.cuisine,
            "prep_time_min": c.prep_time_min, "cook_time_min": c.cook_time_min,
            "total_time_min": c.total_time_min, "calories": c.calories, "ingredient_count": ingredient_count}


@router.get("/pantry-search", response_model=PantrySearchResponse, summary="API-01 ranked recipes from a pantry")
def pantry_search(
    response: Response,
    ingredients: Annotated[list[str], Query(description="repeat the param or comma-separate")],
    max_prep_time: Minutes = None,
    max_calories: Calories = None,
    cuisine: Annotated[str | None, Query(max_length=40)] = None,
    min_match_pct: Annotated[float, Query(ge=0, le=100)] = 0,
    assume_staples: bool = True,
    limit: Limit = 20,
    offset: Offset = 0,
    svc: PantrySearchService = Depends(pantry_service),
) -> PantrySearchResponse:
    r = svc.search(ingredients, max_prep_time=max_prep_time, max_calories=max_calories, cuisine=cuisine,
                   min_match_pct=min_match_pct, assume_staples=assume_staples, limit=limit, offset=offset)
    response.headers["Cache-Control"] = "no-store"
    return PantrySearchResponse(
        snapshot=Snapshot(search_run_id=r.page.run_id, sql_run_id=r.sql_run_id),
        pantry=PantryEcho(resolved=[ResolvedToken(input=i, ingredient_id=iid) for i, iid in r.pantry.resolved],
                          unrecognised=list(r.pantry.unrecognised), staples_assumed=list(r.staples_assumed)),
        total_matches=r.page.total_matches, limit=limit, offset=offset,
        recipes=[PantryRecipe(**_card(s.card, s.ingredient_count), match_percentage=s.match_percentage,
                              matched_ingredients=list(s.matched_ingredients),
                              missing_ingredients=list(s.missing_ingredients),
                              optional_missing=list(s.optional_missing)) for s in r.page.recipes])


@router.get("/cuisine-search", response_model=CuisineSearchResponse, summary="API-02 recipes of one cuisine")
def cuisine_search(
    cuisine: Annotated[str, Query(max_length=40)],
    max_prep_time: Minutes = None,
    max_calories: Calories = None,
    limit: Limit = 20,
    offset: Offset = 0,
    svc: RecipeService = Depends(recipe_service),
) -> CuisineSearchResponse:
    page = svc.by_cuisine(cuisine, max_prep_time, max_calories, limit, offset)
    run_id = svc.snapshot().get("run_id")
    return CuisineSearchResponse(
        snapshot=Snapshot(search_run_id=None, sql_run_id=run_id), cuisine=page.cuisine, limit=page.limit,
        offset=page.offset, next_offset=page.next_offset,
        recipes=[RecipeCardOut(**_card(r.card, r.ingredient_count)) for r in page.recipes])


@router.get("/recipes/{recipe_id}", response_model=RecipeDetailResponse, summary="API-03 full recipe detail")
def recipe_detail(recipe_id: str, svc: RecipeService = Depends(recipe_service)) -> RecipeDetailResponse:
    d = svc.detail(recipe_id)
    c = d.card
    return RecipeDetailResponse(
        snapshot=Snapshot(search_run_id=None, sql_run_id=svc.snapshot().get("run_id")),
        recipe=RecipeHeader(recipe_id=c.recipe_id, recipe_name=c.recipe_name, cuisine=c.cuisine,
                            cuisine_source=d.cuisine_source, prep_time_min=c.prep_time_min,
                            cook_time_min=c.cook_time_min, total_time_min=c.total_time_min, calories=c.calories,
                            servings=d.servings),
        ingredients=[IngredientOut(**vars(i)) for i in d.ingredients],
        steps=[StepOut(step_no=n, step_text=t) for n, t in d.steps],
        utensils=list(d.utensils))


@router.get("/substitute", response_model=SubstituteResponse, summary="API-04 substitutes for one ingredient")
def substitute(
    response: Response,
    ingredient: Annotated[str, Query(max_length=64)],
    pantry: Annotated[list[str] | None, Query()] = None,
    diet: Annotated[list[str] | None, Query()] = None,
    context: str | None = None,
    amount: Annotated[float | None, Query(gt=0, le=10000)] = None,
    unit: str | None = None,
    svc: SubstitutionService = Depends(substitution_service),
) -> SubstituteResponse:
    r = svc.substitute(ingredient, pantry, diet, context, amount, unit)
    response.headers["Cache-Control"] = "no-store"
    subs = [SubstituteOut(
        rule_id=h.rule.rule_id, substitute_id=h.rule.substitute_id,
        substitute_name=r.names.get(h.rule.substitute_id, h.rule.substitute_id.replace("_", " ")),
        ratio=h.rule.ratio, ratio_basis=h.rule.ratio_basis,
        suggested_amount=SuggestedAmount(amount=h.suggested_amount, unit=h.suggested_unit)
        if h.suggested_amount is not None and h.suggested_unit else None,
        context=h.rule.context, diet_tags=sorted(h.rule.diet_tags), confidence=h.rule.confidence,
        confidence_label=CONFIDENCE_LABEL[h.rule.confidence], in_pantry=h.in_pantry, notes=h.rule.notes)
        for h in r.hits]
    return SubstituteResponse(
        ingredient=IngredientRef(input=r.input, ingredient_id=r.ingredient_id, canonical_name=r.canonical_name),
        rule_version=r.rule_version, substitutes=subs,
        reason="unknown_ingredient" if r.ingredient_id is None else (None if subs else "no_rule"),
        disclaimer=DISCLAIMER)


@router.get("/cuisines", response_model=CuisinesResponse, summary="API-05 cuisine dropdown data")
def cuisines(response: Response, svc: RecipeService = Depends(recipe_service)) -> CuisinesResponse:
    response.headers["Cache-Control"] = "max-age=60"
    return CuisinesResponse(cuisines=[CuisineOut(cuisine=c.cuisine, recipe_count=c.recipe_count,
                                                 is_unspecified=c.is_unspecified) for c in svc.cuisines()])


@router.get("/ingredients/suggest", response_model=SuggestResponse, summary="API-06 ingredient autocomplete")
def suggest(response: Response, q: Annotated[str, Query(min_length=1, max_length=32)],
            limit: Annotated[int, Query(ge=1, le=50)] = 10,
            svc: RecipeService = Depends(recipe_service)) -> SuggestResponse:
    response.headers["Cache-Control"] = "max-age=60"
    return SuggestResponse(suggestions=[Suggestion(ingredient_id=e.ingredient_id, canonical_name=e.canonical_name,
                                                   recipe_count=e.recipe_count) for e in svc.suggest(q, limit)])
