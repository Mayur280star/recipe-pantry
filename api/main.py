"""Composition root (docs/09 §6): the ONLY module that names concrete adapters.

    PROFILE=lite uvicorn api.main:app --port 8000               (no Docker)
    PROFILE=standard uvicorn api.main:app --workers 2          (Solr + Trino, M2)
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.config import Settings, get_settings
from api.container import Container, ContainerHolder
from api.metrics import Metrics
from api.routers import health, search, system
from recipe_core.errors import NotFound, RecipeError, UpstreamTimeout, UpstreamUnavailable, ValidationFailed
from recipe_core.normalise import Canonicalizer
from recipe_core.ranking import Guards

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
log = logging.getLogger("api")


# ---------------------------------------------------------------------------------------------
# Logging: one JSON object per line, always carrying the request id (docs/12)
# ---------------------------------------------------------------------------------------------
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"), "level": record.levelname,
               "logger": record.name, "msg": record.getMessage(), "request_id": request_id_var.get()}
        for key in ("path", "status", "duration_ms", "version_key"):
            if hasattr(record, key):
                out[key] = getattr(record, key)
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    app_log = logging.getLogger("api")          # our loggers only; never hijack the root logger
    app_log.handlers[:] = [handler]
    app_log.setLevel(level)
    app_log.propagate = False


# ---------------------------------------------------------------------------------------------
# Profiles -> concrete adapters
# ---------------------------------------------------------------------------------------------
def build_lite(s: Settings) -> tuple[callable, callable]:
    from api.adapters.lite import (
        DuckDbCatalog,
        DuckDbSession,
        InMemoryPantryIndex,
        LiteSnapshot,
        ParquetRuleRepository,
        ParquetVocabularyRepository,
    )

    snap = LiteSnapshot(s.snapshot_root)

    def build() -> Container:
        marker = snap.marker()
        db = DuckDbSession(snap, marker["run_id"])
        vocab = ParquetVocabularyRepository(db).load()
        return Container(
            index=InMemoryPantryIndex.from_db(db, s.min_recipe_size),
            catalog=DuckDbCatalog(db, marker),
            rules=ParquetRuleRepository(db, marker.get("rule_version") or "unknown").load_all(),
            vocabulary=vocab,
            canonicalizer=Canonicalizer(vocab.alias_to_id, vocab.id_to_name),
            guards=Guards(min_recipe_size=s.min_recipe_size),
            staples=s.staples_set,
            max_pantry_items=s.max_pantry_items,
            version_key=f"{marker['run_id']}|{marker.get('rule_version')}",
        )

    return build, snap.version_key


def build_standard(s: Settings) -> tuple[callable, callable]:
    """Solr (pantry search) + Trino (catalog, rules, vocabulary). Once loaded, rules and vocabulary stay in memory,
    so pantry search and substitutions keep working if Trino goes down later (docs/03 §8)."""
    from api.adapters.solr_index import SolrPantryIndex
    from api.adapters.trino import TrinoCatalog, TrinoPool, TrinoRuleRepository, TrinoVocabularyRepository

    pool = TrinoPool(s.trino_host, s.trino_port, s.trino_catalog, s.trino_schema, s.trino_pool_size,
                     s.trino_timeout_s)
    catalog = TrinoCatalog(pool)
    index = SolrPantryIndex(s.solr_url, s.solr_alias, s.solr_timeout_s, s.min_recipe_size)

    def probe() -> str:
        snap = catalog.snapshot()
        return f"{snap['run_id']}|{snap['rule_version']}"

    def build() -> Container:
        snap = catalog.snapshot()
        vocab = TrinoVocabularyRepository(pool).load()
        return Container(
            index=index, catalog=catalog, rules=TrinoRuleRepository(pool).load_all(), vocabulary=vocab,
            canonicalizer=Canonicalizer(vocab.alias_to_id, vocab.id_to_name),
            guards=Guards(min_recipe_size=s.min_recipe_size), staples=s.staples_set,
            max_pantry_items=s.max_pantry_items, version_key=f"{snap['run_id']}|{snap['rule_version']}")

    return build, probe


def make_holder(s: Settings) -> ContainerHolder:
    build, probe = build_lite(s) if s.profile == "lite" else build_standard(s)
    return ContainerHolder(build, probe, s.cache_reload_s)


# ---------------------------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    s = app.state.settings
    app.state.holder = make_holder(s)
    try:
        app.state.holder.get()                     # warm the caches; the API still starts if this fails
    except Exception:
        log.exception("initial snapshot load failed; /health/ready will report down")
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or get_settings()
    configure_logging(s.log_level)
    app = FastAPI(title="Recipe Platform API", version="1.0.0", lifespan=lifespan,
                  description="Pantry match (search tier), cuisine lookup + detail (SQL tier), deterministic "
                              "substitutions. Design: docs/08-api-spec.md")
    app.state.settings = s
    app.state.metrics = Metrics()
    if s.cors_list:
        app.add_middleware(CORSMiddleware, allow_origins=s.cors_list, allow_methods=["GET"],
                           allow_headers=["X-Request-ID"], allow_credentials=False)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        rid = "".join(ch for ch in rid if ch.isalnum() or ch in "-_")[:64] or uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
            route = request.scope.get("route")
            request.app.state.metrics.observe(request.method, getattr(route, "path", "unmatched"),
                                              response.status_code, time.perf_counter() - t0)
            response.headers["X-Request-ID"] = rid
            log.info("request", extra={"path": request.url.path, "status": response.status_code,
                                       "duration_ms": round((time.perf_counter() - t0) * 1000, 1)})
            return response
        finally:
            request_id_var.reset(token)

    def error(status: int, code: str, message: str, headers: dict | None = None) -> JSONResponse:
        return JSONResponse(status_code=status, headers=headers,
                            content={"error": {"code": code, "message": message[:300],
                                               "request_id": request_id_var.get()}})

    @app.exception_handler(ValidationFailed)
    async def _validation(_: Request, exc: ValidationFailed):
        return error(422, "validation_error", str(exc))

    @app.exception_handler(RequestValidationError)
    async def _request_validation(_: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(p) for p in first.get("loc", ())[1:]) or "request"
        return error(422, "validation_error", f"{field}: {first.get('msg', 'invalid')}")

    @app.exception_handler(NotFound)
    async def _not_found(_: Request, exc: NotFound):
        return error(404, "not_found", str(exc))

    @app.exception_handler(UpstreamTimeout)
    async def _timeout(_: Request, exc: UpstreamTimeout):
        log.warning("upstream timeout: %s", exc)
        return error(504, "upstream_timeout", "a backing service did not answer in time")

    @app.exception_handler(UpstreamUnavailable)
    async def _unavailable(_: Request, exc: UpstreamUnavailable):
        log.warning("upstream unavailable: %s", exc)                  # details in logs only
        return error(503, "upstream_unavailable", "a backing service is unavailable", {"Retry-After": "5"})

    @app.exception_handler(RecipeError)
    async def _recipe_error(_: Request, exc: RecipeError):
        log.error("unhandled domain error: %r", exc)
        return error(500, "internal_error", "internal error")

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        code = "not_found" if exc.status_code == 404 else "http_error"
        return error(exc.status_code, code, "not found" if exc.status_code == 404 else str(exc.detail))

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception):
        log.exception("unexpected error")                               # stack trace in logs, never in body
        return error(500, "internal_error", "internal error")

    app.include_router(search.router)
    @app.get("/metrics", include_in_schema=False, response_class=PlainTextResponse)
    def metrics(request: Request) -> str:
        """Prometheus scrape endpoint (RED per route + platform gauges)."""
        holder = request.app.state.holder
        gauges: dict[str, tuple[float, dict[str, str]]] = {
            "container_reloads_total": (holder.reloads, {}),
            "container_loaded": (1 if holder.loaded else 0, {}),
        }
        if holder.loaded:
            c = holder.get()
            run_id, rules_version = c.version_key.split("|", 1)
            gauges["snapshot_info"] = (1, {"run_id": run_id, "rule_version": rules_version})
            gauges["substitution_rules"] = (len(c.rules), {})
            gauges["vocabulary_ingredients"] = (len(c.vocabulary.entries), {})
            gauges["pantry_parity_violations_total"] = (getattr(c.index, "parity_violations", 0), {})
        return request.app.state.metrics.render(gauges)

    app.include_router(system.router)
    app.include_router(health.router)
    return app


app = create_app()
