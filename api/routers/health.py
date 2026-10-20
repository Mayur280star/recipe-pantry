"""API-07 health endpoints with REAL probes (the original returned constants — F-023)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response

from api.container import ContainerHolder
from api.routers.deps import holder
from api.schemas.models import LiveResponse, ReadyResponse, VersionsResponse
from recipe_core import core_version

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", response_model=LiveResponse)
def live() -> LiveResponse:
    return LiveResponse(status="up")                       # never depends on anything else


def _probe(fn) -> str:
    try:
        fn()
        return "up"
    except Exception:
        return "down"


@router.get("/ready", response_model=ReadyResponse)
def ready(request: Request, response: Response, h: ContainerHolder = Depends(holder)) -> ReadyResponse:
    try:
        c = h.get()
    except Exception:
        c = None
    if c is None:
        comps = {"search": "down", "sql": "down", "rule_cache": "down"}
    else:
        comps = {"search": _probe(c.index.ping), "sql": _probe(c.catalog.ping),
                 "rule_cache": "up" if len(c.rules) > 0 else "down"}
    ups = sum(v == "up" for v in comps.values())
    status = "ok" if ups == len(comps) else "down" if ups == 0 else "degraded"
    if status == "down":
        response.status_code = 503
    return ReadyResponse(status=status, profile=request.app.state.settings.profile, components=comps,
                         checked_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))


@router.get("/versions", response_model=VersionsResponse)
def versions(h: ContainerHolder = Depends(holder)) -> VersionsResponse:
    c = h.get()
    try:
        search_run = c.index.run_id()
    except Exception:
        search_run = None
    try:
        snap = c.catalog.snapshot()
    except Exception:
        snap = {}
    sql_run = snap.get("run_id")
    return VersionsResponse(search_run_id=search_run, sql_run_id=sql_run, rule_version=c.rules.version,
                            core_version=core_version, aligned=bool(search_run) and search_run == sql_run)
