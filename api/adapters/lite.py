"""Lite-profile adapters: Parquet snapshot + DuckDB + in-memory inverted index (docs/03 §6, docs/06 §3.4).

Same ports as the Solr/Trino adapters; the contract tests in tests/contract run against both.
"""

from __future__ import annotations

import json
import threading
from collections import defaultdict
from pathlib import Path

import duckdb

from recipe_core.errors import UpstreamUnavailable
from recipe_core.models import (
    CuisineCount,
    CuisinePage,
    CuisineQuery,
    CuisineRecipe,
    IngredientLine,
    PantryPage,
    PantryQuery,
    RecipeCandidate,
    RecipeCard,
    RecipeDetail,
    VocabEntry,
    Vocabulary,
)
from recipe_core.ranking import DefaultMatchScorer, Guards, sort_key
from recipe_core.rules import Rule, RuleSet

TABLES = ("recipes", "recipe_ingredients", "recipe_steps", "recipe_utensils", "cuisine_summary",
          "ingredient_dim", "ingredient_alias", "substitution_rules", "etl_runs", "search_docs", "dq_gates")


class LiteSnapshot:
    """Resolves tables of the currently published snapshot via `${SNAPSHOT_ROOT}/_PUBLISHED`."""

    def __init__(self, root: str):
        self.root = Path(root)

    def marker(self) -> dict:
        p = self.root / "_PUBLISHED"
        if not p.exists():
            raise UpstreamUnavailable(f"no published snapshot under {self.root} (run the ETL first)")
        return json.loads(p.read_text())

    def version_key(self) -> str:
        m = self.marker()
        return f"{m['run_id']}|{m.get('rule_version')}"

    def table_glob(self, run_id: str, table: str) -> str:
        return str(self.root / "curated" / f"run_id={run_id}" / table / "*.parquet")


class DuckDbSession:
    """One in-memory DuckDB with a view per table of one snapshot. Cursors are per call (thread-safe)."""

    def __init__(self, snapshot: LiteSnapshot, run_id: str):
        self.run_id = run_id
        self._con = duckdb.connect(":memory:")
        self._lock = threading.Lock()
        for t in TABLES:
            glob = snapshot.table_glob(run_id, t).replace("'", "''")
            self._con.execute(f"CREATE VIEW {t} AS SELECT * FROM read_parquet('{glob}')")

    def query(self, sql: str, params: list | None = None) -> list[tuple]:
        with self._lock:
            cur = self._con.cursor()
        try:
            return cur.execute(sql, params or []).fetchall()
        except duckdb.Error as exc:
            raise UpstreamUnavailable(f"duckdb: {exc}") from exc
        finally:
            cur.close()


def _card(row, offset: int = 0) -> RecipeCard:
    rid, name, cuisine, prep, cook, total, cal = row[offset:offset + 7]
    return RecipeCard(rid, name, cuisine, prep, cook, total, cal)


class DuckDbCatalog:
    """RecipeCatalog over the lite snapshot. SQL mirrors docs/04 §4 Q1–Q3."""

    CARD = "recipe_id, recipe_name, cuisine, prep_time_min, cook_time_min, total_time_min, calories"

    def __init__(self, db: DuckDbSession, marker: dict):
        self._db, self._marker = db, marker

    def by_cuisine(self, q: CuisineQuery) -> CuisinePage:
        where, params = ["cuisine = ?"], [q.cuisine]
        if q.max_prep_time is not None:           # Food.com has no prep split: fall back to total time
            where.append("coalesce(prep_time_min, total_time_min) <= ?")
            params.append(q.max_prep_time)
        if q.max_calories is not None:
            where.append("calories <= ?")
            params.append(q.max_calories)
        rows = self._db.query(
            f"SELECT {self.CARD}, ingredient_count FROM recipes WHERE {' AND '.join(where)} "
            "ORDER BY total_time_min ASC NULLS LAST, recipe_id ASC LIMIT ? OFFSET ?",
            [*params, q.limit + 1, q.offset])
        more = len(rows) > q.limit
        items = tuple(CuisineRecipe(_card(r), r[7]) for r in rows[:q.limit])
        return CuisinePage(q.cuisine, items, q.limit, q.offset, q.offset + q.limit if more else None)

    def detail(self, recipe_id: str) -> RecipeDetail | None:
        head = self._db.query(f"SELECT {self.CARD}, cuisine_source, servings FROM recipes WHERE recipe_id = ?",
                              [recipe_id])
        if not head:
            return None
        ings = self._db.query(
            "SELECT line_no, raw_text, ingredient_id, ingredient_name, quantity, unit, is_optional "
            "FROM recipe_ingredients WHERE recipe_id = ? ORDER BY line_no", [recipe_id])
        steps = self._db.query("SELECT step_no, step_text FROM recipe_steps WHERE recipe_id = ? ORDER BY step_no",
                               [recipe_id])
        utensils = self._db.query(
            "SELECT utensil_name FROM recipe_utensils WHERE recipe_id = ? ORDER BY utensil_name", [recipe_id])
        h = head[0]
        return RecipeDetail(_card(h), h[7], h[8], tuple(IngredientLine(*r) for r in ings),
                            tuple((r[0], r[1]) for r in steps), tuple(r[0] for r in utensils))

    def cuisines(self) -> list[CuisineCount]:
        rows = self._db.query("SELECT cuisine, recipe_count FROM cuisine_summary ORDER BY recipe_count DESC, cuisine")
        return [CuisineCount(c, int(n)) for c, n in rows]

    def ping(self) -> None:
        self._db.query("SELECT 1")

    def run_summary(self) -> dict:
        cols = ["run_id", "status", "previous_run_id", "source", "input_rows", "recipes", "quarantined",
                "duplicates_dropped", "rule_version", "core_version", "started_at", "finished_at", "duration_s"]
        run = self._db.query(f"SELECT {', '.join(cols)} FROM etl_runs WHERE run_id = ?", [self._db.run_id])
        gates = self._db.query("SELECT gate_id, gate_name, status, value, detail FROM dq_gates ORDER BY gate_id")
        counts = self._db.query(
            "SELECT (SELECT count(*) FROM recipes), (SELECT count(*) FROM recipe_ingredients), "
            "(SELECT count(*) FROM ingredient_dim), (SELECT count(*) FROM substitution_rules)")[0]
        return {"run": dict(zip(cols, run[0], strict=True)) if run else {},
                "gates": [dict(zip(("gate_id", "gate_name", "status", "value", "detail"), g, strict=True))
                          for g in gates],
                "tables": dict(zip(("recipes", "recipe_ingredients", "ingredient_dim", "substitution_rules"),
                                   counts, strict=True))}

    def snapshot(self) -> dict[str, str]:
        return {"run_id": self._db.run_id, "rule_version": self._marker.get("rule_version") or "",
                "core_version": self._marker.get("core_version") or ""}


class InMemoryPantryIndex:
    """Exact pantry-match over an inverted index {ingredient_id -> doc positions} (docs/06 §3.4)."""

    def __init__(self, docs: list[RecipeCandidate], run_id: str, min_recipe_size: int = 3):
        self._docs = docs
        self._run_id = run_id
        self._min_recipe_size = min_recipe_size
        postings: dict[str, list[int]] = defaultdict(list)
        for i, d in enumerate(docs):
            for iid in d.ingredient_ids:
                postings[iid].append(i)
        self._postings = dict(postings)

    @classmethod
    def from_db(cls, db: DuckDbSession, min_recipe_size: int = 3) -> InMemoryPantryIndex:
        rows = db.query("SELECT id, recipe_name, cuisine, prep_time, cook_time, total_time, calories, "
                        "ingredient_ids, optional_ingredient_ids FROM search_docs ORDER BY id")
        docs = [RecipeCandidate(_card(r), frozenset(r[7]), frozenset(r[8] or ()), db.run_id) for r in rows]
        return cls(docs, db.run_id, min_recipe_size)

    def search(self, q: PantryQuery) -> PantryPage:
        cand: set[int] = set()
        for iid in q.pantry_ids:
            cand.update(self._postings.get(iid, ()))
        scorer = DefaultMatchScorer(Guards(min_recipe_size=self._min_recipe_size, min_match_pct=q.min_match_pct))
        scored = []
        for i in cand:
            d = self._docs[i]
            c = d.card
            if q.cuisine and c.cuisine != q.cuisine:
                continue
            prep = c.prep_time_min if c.prep_time_min is not None else c.total_time_min
            if q.max_prep_time is not None and (prep is None or prep > q.max_prep_time):
                continue
            if q.max_calories is not None and (c.calories is None or c.calories > q.max_calories):
                continue
            s = scorer.score(q.pantry_ids, q.staples, d)
            if s is not None:
                scored.append(s)
        scored.sort(key=sort_key)
        return PantryPage(len(scored), tuple(scored[q.offset:q.offset + q.limit]), self._run_id)

    def ping(self) -> None:
        if not self._docs:
            raise UpstreamUnavailable("pantry index is empty")

    def run_id(self) -> str | None:
        return self._run_id


class ParquetRuleRepository:
    def __init__(self, db: DuckDbSession, version: str):
        self._db, self._version = db, version

    def load_all(self) -> RuleSet:
        rows = self._db.query("SELECT rule_id, original_id, substitute_id, ratio, ratio_basis, context, diet_tags, "
                              "confidence, notes FROM substitution_rules ORDER BY rule_id")
        rules = [Rule(r[0], r[1], r[2], r[3], r[4], r[5], frozenset(t for t in (r[6] or "").split("|") if t),
                      int(r[7]), r[8] or "") for r in rows]
        return RuleSet.from_rules(self._version, rules)

    def current_version(self) -> str:
        return self._version


class ParquetVocabularyRepository:
    def __init__(self, db: DuckDbSession):
        self._db = db

    def load(self) -> Vocabulary:
        entries = tuple(VocabEntry(r[0], r[1], int(r[2] or 0), r[3]) for r in self._db.query(
            "SELECT ingredient_id, canonical_name, recipe_count, category FROM ingredient_dim ORDER BY ingredient_id"))
        aliases = dict(self._db.query("SELECT alias, ingredient_id FROM ingredient_alias"))
        return Vocabulary(entries, aliases)
