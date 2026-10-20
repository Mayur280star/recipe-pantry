"""Pydantic response models for API-01…07 (docs/08). The generated OpenAPI is snapshot-tested (TST-07)."""

from __future__ import annotations

from pydantic import BaseModel


class Snapshot(BaseModel):
    search_run_id: str | None
    sql_run_id: str | None


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorBody


# ---- API-01 --------------------------------------------------------------------------------
class ResolvedToken(BaseModel):
    input: str
    ingredient_id: str


class PantryEcho(BaseModel):
    resolved: list[ResolvedToken]
    unrecognised: list[str]
    staples_assumed: list[str]


class RecipeCardOut(BaseModel):
    recipe_id: str
    recipe_name: str
    cuisine: str | None
    prep_time_min: int | None
    cook_time_min: int | None
    total_time_min: int | None
    calories: int | None
    ingredient_count: int


class PantryRecipe(RecipeCardOut):
    match_percentage: float
    matched_ingredients: list[str]
    missing_ingredients: list[str]
    optional_missing: list[str]


class PantrySearchResponse(BaseModel):
    snapshot: Snapshot
    pantry: PantryEcho
    total_matches: int
    limit: int
    offset: int
    recipes: list[PantryRecipe]


# ---- API-02 / 03 / 05 / 06 --------------------------------------------------------------------
class CuisineSearchResponse(BaseModel):
    snapshot: Snapshot
    cuisine: str
    limit: int
    offset: int
    next_offset: int | None
    recipes: list[RecipeCardOut]


class RecipeHeader(BaseModel):
    recipe_id: str
    recipe_name: str
    cuisine: str | None
    cuisine_source: str | None
    prep_time_min: int | None
    cook_time_min: int | None
    total_time_min: int | None
    calories: int | None
    servings: int | None


class IngredientOut(BaseModel):
    line_no: int
    raw_text: str
    ingredient_id: str | None
    ingredient_name: str
    quantity: float | None
    unit: str
    is_optional: bool


class StepOut(BaseModel):
    step_no: int
    step_text: str


class RecipeDetailResponse(BaseModel):
    snapshot: Snapshot
    recipe: RecipeHeader
    ingredients: list[IngredientOut]
    steps: list[StepOut]
    utensils: list[str]


class CuisineOut(BaseModel):
    cuisine: str
    recipe_count: int
    is_unspecified: bool


class CuisinesResponse(BaseModel):
    cuisines: list[CuisineOut]


class Suggestion(BaseModel):
    ingredient_id: str
    canonical_name: str
    recipe_count: int


class SuggestResponse(BaseModel):
    suggestions: list[Suggestion]


# ---- API-04 --------------------------------------------------------------------------------
class IngredientRef(BaseModel):
    input: str
    ingredient_id: str | None
    canonical_name: str | None


class SuggestedAmount(BaseModel):
    amount: float
    unit: str


class SubstituteOut(BaseModel):
    rule_id: str
    substitute_id: str
    substitute_name: str
    ratio: float | None
    ratio_basis: str
    suggested_amount: SuggestedAmount | None
    context: str
    diet_tags: list[str]
    confidence: int
    confidence_label: str
    in_pantry: bool
    notes: str


class SubstituteResponse(BaseModel):
    ingredient: IngredientRef
    rule_version: str
    substitutes: list[SubstituteOut]
    reason: str | None = None
    disclaimer: str


# ---- API-07 --------------------------------------------------------------------------------
class LiveResponse(BaseModel):
    status: str


class ReadyResponse(BaseModel):
    status: str                          # ok | degraded | down
    profile: str
    components: dict[str, str]
    checked_at: str


class VersionsResponse(BaseModel):
    search_run_id: str | None
    sql_run_id: str | None
    rule_version: str | None
    core_version: str
    aligned: bool


# ---- API-08 (viva "System" tab) -------------------------------------------------------------
class GateOut(BaseModel):
    gate_id: str
    gate_name: str
    status: str
    value: str | None
    detail: str


class SystemSummaryResponse(BaseModel):
    profile: str
    run: dict[str, str | int | float | None]
    gates: list[GateOut]
    tables: dict[str, int]
