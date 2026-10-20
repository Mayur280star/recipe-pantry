"""Trino adapters for the standard profile (ADR-10): RecipeCatalog, RuleRepository, VocabularyRepository.

All SQL goes through the `recipe_db` VIEWS, which STG-08 re-points per snapshot (ADR-11), so a publish or rollback is
visible on the next query. User values are bound parameters; LIMIT/OFFSET are validated ints (docs/08 §5).
"""

from __future__ import annotations

import contextlib
import logging
import queue
from concurrent.futures import ThreadPoolExecutor

from recipe_core.errors import UpstreamTimeout, UpstreamUnavailable
from recipe_core.models import (
    CuisineCount,
    CuisinePage,
    CuisineQuery,
    CuisineRecipe,
    IngredientLine,
    RecipeCard,
    RecipeDetail,
    VocabEntry,
    Vocabulary,
)
from recipe_core.rules import Rule, RuleSet

log = logging.getLogger("api.trino")
_DETAIL_POOL = ThreadPoolExecutor(max_workers=16, thread_name_prefix="trino-detail")
CARD = "recipe_id, recipe_name, cuisine, prep_time_min, cook_time_min, total_time_min, calories"


class TrinoPool:
    """Bounded pool (docs/09 §7): caps SQL concurrency; borrow timeout -> 503; broken connections discarded."""

    def __init__(self, host: str, port: int, catalog: str, schema: str, size: int = 8, timeout_s: float = 5.0,
                 borrow_timeout_s: float = 2.0, user: str = "recipe-api"):
        self._args = dict(host=host, port=port, user=user, catalog=catalog, schema=schema,
                          request_timeout=timeout_s)
        self._q: queue.Queue = queue.Queue(maxsize=size)
        self._borrow_timeout = borrow_timeout_s
        for _ in range(size):
            self._q.put(None)                          # lazily connected slots

    def _connect(self):
        import trino

        return trino.dbapi.connect(**self._args)

    @contextlib.contextmanager
    def connection(self):
        try:
            conn = self._q.get(timeout=self._borrow_timeout)
        except queue.Empty as exc:
            raise UpstreamUnavailable("trino pool exhausted") from exc
        try:
            if conn is None:
                conn = self._connect()
            yield conn
        except Exception:
            conn = None                                # discard; the slot is refilled lazily
            raise
        finally:
            self._q.put(conn)

    def query(self, sql: str, params: list | None = None) -> list[tuple]:
        try:
            with self.connection() as conn:
                cur = conn.cursor()
                cur.execute(sql, params) if params else cur.execute(sql)
                return cur.fetchall()
        except (UpstreamUnavailable, UpstreamTimeout):
            raise
        except Exception as exc:                         # driver/HTTP errors -> domain errors (details in logs)
            if "timeout" in type(exc).__name__.lower() or "timed out" in str(exc).lower():
                raise UpstreamTimeout("trino timeout") from exc
            raise UpstreamUnavailable(f"trino: {type(exc).__name__}: {str(exc)[:200]}") from exc


def _card(row) -> RecipeCard:
    return RecipeCard(*row[:7])


class TrinoCatalog:
    def __init__(self, pool: TrinoPool):
        self.pool = pool

    def by_cuisine(self, q: CuisineQuery) -> CuisinePage:
        where, params = ["cuisine = ?"], [q.cuisine]
        if q.max_prep_time is not None:                   # ADR-15
            where.append("coalesce(prep_time_min, total_time_min) <= ?")
            params.append(q.max_prep_time)
        if q.max_calories is not None:
            where.append("calories <= ?")
            params.append(q.max_calories)
        rows = self.pool.query(
            f"SELECT {CARD}, ingredient_count FROM recipes WHERE {' AND '.join(where)} "
            f"ORDER BY total_time_min ASC NULLS LAST, recipe_id ASC OFFSET {int(q.offset)} LIMIT {int(q.limit) + 1}",
            params)
        more = len(rows) > q.limit
        items = tuple(CuisineRecipe(_card(r), r[7]) for r in rows[:q.limit])
        return CuisinePage(q.cuisine, items, q.limit, q.offset, q.offset + q.limit if more else None)

    def detail(self, recipe_id: str) -> RecipeDetail | None:
        """Four small keyed queries (docs/04 Q2), run CONCURRENTLY: latency ≈ the slowest one, not the sum (ADR-21)."""
        queries = {
            "head": (f"SELECT {CARD}, cuisine_source, servings FROM recipes WHERE recipe_id = ?", [recipe_id]),
            "ings": ("SELECT line_no, raw_text, ingredient_id, ingredient_name, quantity, unit, is_optional "
                     "FROM recipe_ingredients WHERE recipe_id = ? ORDER BY line_no", [recipe_id]),
            "steps": ("SELECT step_no, step_text FROM recipe_steps WHERE recipe_id = ? ORDER BY step_no", [recipe_id]),
            "utensils": ("SELECT utensil_name FROM recipe_utensils WHERE recipe_id = ? ORDER BY utensil_name",
                         [recipe_id]),
        }
        futures = {k: _DETAIL_POOL.submit(self.pool.query, sql, params) for k, (sql, params) in queries.items()}
        r = {k: f.result() for k, f in futures.items()}             # re-raises Upstream* errors
        if not r["head"]:
            return None
        h = r["head"][0]
        return RecipeDetail(_card(h), h[7], h[8], tuple(IngredientLine(*x) for x in r["ings"]),
                            tuple((x[0], x[1]) for x in r["steps"]), tuple(x[0] for x in r["utensils"]))

    def cuisines(self) -> list[CuisineCount]:
        rows = self.pool.query("SELECT cuisine, recipe_count FROM cuisine_summary ORDER BY recipe_count DESC, cuisine")
        return [CuisineCount(c, int(n)) for c, n in rows]

    def ping(self) -> None:
        self.pool.query("SELECT 1")

    def snapshot(self) -> dict[str, str]:
        rows = self.pool.query("SELECT run_id, rule_version, core_version FROM current_snapshot")
        if not rows:
            raise UpstreamUnavailable("no published snapshot (current_snapshot view is empty)")
        run_id, rule_version, core_version = rows[0]
        return {"run_id": run_id, "rule_version": rule_version or "", "core_version": core_version or ""}

    def run_summary(self) -> dict:
        cols = ["run_id", "status", "previous_run_id", "source", "input_rows", "recipes", "quarantined",
                "duplicates_dropped", "rule_version", "core_version", "started_at", "finished_at", "duration_s"]
        run = self.pool.query(f"SELECT {', '.join(cols)} FROM etl_runs")
        gates = self.pool.query("SELECT gate_id, gate_name, status, value, detail FROM dq_gates ORDER BY gate_id")
        counts = self.pool.query(
            "SELECT (SELECT count(*) FROM recipes), (SELECT count(*) FROM recipe_ingredients), "
            "(SELECT count(*) FROM ingredient_dim), (SELECT count(*) FROM substitution_rules)")[0]
        return {"run": dict(zip(cols, run[0], strict=True)) if run else {},
                "gates": [dict(zip(("gate_id", "gate_name", "status", "value", "detail"), g, strict=True))
                          for g in gates],
                "tables": dict(zip(("recipes", "recipe_ingredients", "ingredient_dim", "substitution_rules"),
                                   (int(c) for c in counts), strict=True))}


class TrinoRuleRepository:
    def __init__(self, pool: TrinoPool):
        self.pool = pool

    def current_version(self) -> str:
        return self.pool.query("SELECT rule_version FROM current_snapshot")[0][0] or "unknown"

    def load_all(self) -> RuleSet:
        rows = self.pool.query("SELECT rule_id, original_id, substitute_id, ratio, ratio_basis, context, diet_tags, "
                               "confidence, notes FROM substitution_rules ORDER BY rule_id")
        rules = [Rule(r[0], r[1], r[2], r[3], r[4], r[5], frozenset(t for t in (r[6] or "").split("|") if t),
                      int(r[7]), r[8] or "") for r in rows]
        return RuleSet.from_rules(self.current_version(), rules)


class TrinoVocabularyRepository:
    def __init__(self, pool: TrinoPool):
        self.pool = pool

    def load(self) -> Vocabulary:
        entries = tuple(VocabEntry(r[0], r[1], int(r[2] or 0), r[3]) for r in self.pool.query(
            "SELECT ingredient_id, canonical_name, recipe_count, category FROM ingredient_dim ORDER BY ingredient_id"))
        aliases = dict(self.pool.query("SELECT alias, ingredient_id FROM ingredient_alias"))
        return Vocabulary(entries, aliases)
