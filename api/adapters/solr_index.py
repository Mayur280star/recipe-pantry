"""SolrPantryIndex: the exact pantry-match ranking executed INSIDE Solr (docs/06 §3.1). Fixes F-001, F-002, F-010.

- candidates  : {!terms f=ingredient_ids}  over P (non-staple pantry ids)
- min_matches : {!frange} post-filter on sum(termfreq(ingredient_ids, p) for p in P)
- ordering    : matched(E)/ingredient_count desc, matched(E) desc, id asc   with E = P ∪ S
Only ids matching ^[a-z0-9_]{1,64}$ ever reach a query string. Solr's BM25 `score` is not used.
Hydration recomputes matched/missing in Python and counts any disagreement with Solr's order (parity).
"""

from __future__ import annotations

import logging
import re

import requests

from recipe_core.errors import UpstreamTimeout, UpstreamUnavailable, ValidationFailed
from recipe_core.models import PantryPage, PantryQuery, RecipeCandidate, RecipeCard
from recipe_core.ranking import DefaultMatchScorer, Guards, sort_key

log = logging.getLogger("api.solr")
ID_RE = re.compile(r"^[a-z0-9_]{1,64}$")
FIELDS = ("id,recipe_name,cuisine,prep_time,cook_time,total_time,calories,ingredient_count,ingredient_ids,"
          "optional_ingredient_ids,run_id")


def _safe_ids(ids) -> list[str]:
    out = sorted(ids)
    bad = [i for i in out if not ID_RE.match(i)]
    if bad:                                           # defence in depth: canonicaliser only emits slugs
        raise ValidationFailed(f"invalid ingredient id(s): {bad[:3]}")
    return out


def _matched_expr(ids: list[str]) -> str:
    terms = [f"termfreq(ingredient_ids,'{i}')" for i in ids]
    return terms[0] if len(terms) == 1 else f"sum({','.join(terms)})"


def build_params(q: PantryQuery, min_recipe_size: int, min_matches: int = 2) -> dict[str, object]:
    pantry = _safe_ids(q.pantry_ids)
    effective = _safe_ids(q.pantry_ids | q.staples)
    matched_p = _matched_expr(pantry)
    matched_e = _matched_expr(effective)
    ratio = f"div({matched_e},ingredient_count)"
    fq = [
        "{!terms f=ingredient_ids v=$pantry}",
        f"ingredient_count:[{int(min_recipe_size)} TO *]",
        f"{{!frange l={min(min_matches, len(pantry))} cache=false cost=200}}{matched_p}",
    ]
    if q.min_match_pct:
        fq.append(f"{{!frange l={q.min_match_pct / 100 - 1e-9:.10f} cache=false cost=300}}{ratio}")
    if q.cuisine:
        fq.append(f"cuisine:{_safe_ids([q.cuisine])[0]}")
    if q.max_calories is not None:
        fq.append(f"calories:[* TO {int(q.max_calories)}]")
    if q.max_prep_time is not None:                  # ADR-15: fall back to total time when prep is unknown
        n = int(q.max_prep_time)
        fq.append(f"(prep_time:[* TO {n}] OR (*:* -prep_time:[* TO *] AND total_time:[* TO {n}]))")
    return {"q": "*:*", "defType": "lucene", "fq": fq, "pantry": ",".join(pantry),
            "sort": f"{ratio} desc,{matched_e} desc,id asc", "fl": FIELDS, "start": q.offset, "rows": q.limit,
            "wt": "json"}


class SolrPantryIndex:
    def __init__(self, base_url: str, alias: str = "recipes", timeout: float = 3.0, min_recipe_size: int = 3):
        self.url = f"{base_url.rstrip('/')}/{alias}"
        self.timeout = timeout
        self.min_recipe_size = min_recipe_size
        self.session = requests.Session()
        self.parity_violations = 0

    def _get(self, path: str, params) -> dict:
        try:
            r = self.session.get(f"{self.url}/{path}", params=params, timeout=self.timeout)
        except requests.Timeout as exc:
            raise UpstreamTimeout("solr timeout") from exc
        except requests.ConnectionError as exc:
            raise UpstreamUnavailable(f"solr unreachable: {exc}") from exc
        if r.status_code >= 500 or r.status_code == 404:
            raise UpstreamUnavailable(f"solr {r.status_code}: {r.text[:200]}")
        if not r.ok:
            raise UpstreamUnavailable(f"solr rejected query {r.status_code}: {r.text[:200]}")
        return r.json()

    def search(self, q: PantryQuery) -> PantryPage:
        body = self._get("select", build_params(q, self.min_recipe_size))
        docs = body["response"]["docs"]
        scorer = DefaultMatchScorer(Guards(min_recipe_size=self.min_recipe_size, min_match_pct=q.min_match_pct))
        scored, run_id = [], None
        for d in docs:
            run_id = run_id or d.get("run_id")
            card = RecipeCard(d["id"], d.get("recipe_name", ""), d.get("cuisine"), d.get("prep_time"),
                              d.get("cook_time"), d.get("total_time"), d.get("calories"))
            cand = RecipeCandidate(card, frozenset(d.get("ingredient_ids", ())),
                                   frozenset(d.get("optional_ingredient_ids", ())), d.get("run_id"))
            s = scorer.score(q.pantry_ids, q.staples, cand)
            if s is None:                               # Solr returned something Python would exclude
                self._violation("ineligible", d["id"])
                continue
            scored.append(s)
        if [sort_key(s) for s in scored] != sorted(sort_key(s) for s in scored):
            self._violation("order", ",".join(s.recipe_id for s in scored[:3]))
        return PantryPage(int(body["response"]["numFound"]), tuple(scored), run_id or self.run_id())

    def _violation(self, kind: str, detail: str) -> None:
        self.parity_violations += 1
        log.warning("pantry parity violation", extra={"path": f"{kind}:{detail}"})

    def ping(self) -> None:
        self._get("select", {"q": "*:*", "rows": 0, "wt": "json"})

    def run_id(self) -> str | None:
        docs = self._get("select", {"q": "*:*", "rows": 1, "fl": "run_id", "wt": "json"})["response"]["docs"]
        return docs[0].get("run_id") if docs else None
