"""FastAPI dependency providers. Tests override these (app.dependency_overrides)."""

from __future__ import annotations

from fastapi import Request

from api.container import Container, ContainerHolder
from api.services.pantry import PantrySearchService
from api.services.recipes import RecipeService
from api.services.substitution import SubstitutionService


def holder(request: Request) -> ContainerHolder:
    return request.app.state.holder


def container(request: Request) -> Container:
    return request.app.state.holder.get()


def pantry_service(request: Request) -> PantrySearchService:
    return PantrySearchService(container(request))


def recipe_service(request: Request) -> RecipeService:
    return RecipeService(container(request))


def substitution_service(request: Request) -> SubstitutionService:
    return SubstitutionService(container(request))
