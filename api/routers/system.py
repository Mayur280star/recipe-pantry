"""API-08: live snapshot summary for the UI "System" tab (ETL run row + DQ gates + counts)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from api.routers.deps import recipe_service
from api.schemas.models import SystemSummaryResponse
from api.services.recipes import RecipeService

router = APIRouter(prefix="/api/v1/system", tags=["system"])


@router.get("/summary", response_model=SystemSummaryResponse, summary="API-08 live snapshot, ETL run and DQ gates")
def summary(request: Request, response: Response, svc: RecipeService = Depends(recipe_service)):
    response.headers["Cache-Control"] = "max-age=10"
    return SystemSummaryResponse(profile=request.app.state.settings.profile, **svc.run_summary())
