"""Deterministic substitution rules: model, validation V1–V10, lookup (docs/07). Fixes F-020, F-021.

Validation is a pure function over CSV-shaped rows so it runs identically in CI (on the CSV in Git)
and in the ETL gates G8/G9.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

RATIO_BASES = frozenset({"same_unit", "note"})
CONTEXTS = frozenset({"any", "baking", "cooking", "raw"})
DIET_TAGS = frozenset({"vegan", "vegetarian", "dairy_free", "egg_free", "gluten_free", "nut_free",
                       "low_sodium"})
REQUIRED_COLUMNS = ("rule_id", "original_id", "substitute_id", "ratio", "ratio_basis", "context",
                    "diet_tags", "confidence", "notes")

# V8: a substitute of this ingredient_dim.category may NOT carry these tags.
FORBIDDEN_TAGS_BY_CATEGORY: dict[str, frozenset[str]] = {
    "dairy": frozenset({"dairy_free", "vegan"}),
    "egg": frozenset({"egg_free", "vegan"}),
    "meat": frozenset({"vegetarian", "vegan"}),
    "fish": frozenset({"vegetarian", "vegan"}),
    "seafood": frozenset({"vegetarian", "vegan"}),
    "wheat": frozenset({"gluten_free"}),
    "nut": frozenset({"nut_free"}),
}

DISCLAIMER = "Check product labels if you have an allergy or medical diet."


@dataclass(frozen=True)
class Rule:
    rule_id: str
    original_id: str
    substitute_id: str
    ratio: float | None
    ratio_basis: str
    context: str
    diet_tags: frozenset[str]
    confidence: int
    notes: str


@dataclass(frozen=True)
class Issue:
    code: str            # V1..V10
    severity: str        # error | warning | info
    rule_id: str
    message: str


@dataclass(frozen=True)
class RuleSet:
    version: str
    by_original: Mapping[str, tuple[Rule, ...]]

    @classmethod
    def from_rules(cls, version: str, rules: Iterable[Rule]) -> RuleSet:
        grouped: dict[str, list[Rule]] = {}
        for r in rules:
            grouped.setdefault(r.original_id, []).append(r)
        return cls(version, {k: tuple(v) for k, v in grouped.items()})

    def __len__(self) -> int:
        return sum(len(v) for v in self.by_original.values())


@dataclass(frozen=True)
class SubstituteHit:
    rule: Rule
    in_pantry: bool
    suggested_amount: float | None
    suggested_unit: str | None


# ---------------------------------------------------------------------------------------------
# Parsing + validation
# ---------------------------------------------------------------------------------------------

def _split_tags(value: str | None) -> frozenset[str]:
    return frozenset(t.strip() for t in (value or "").split("|") if t.strip())


def parse_row(row: Mapping[str, str]) -> tuple[Rule | None, list[Issue]]:
    """V1: schema, types and enums. Returns (rule or None, issues)."""
    rid = (row.get("rule_id") or "").strip() or "<missing>"
    issues: list[Issue] = []

    def err(msg: str) -> None:
        issues.append(Issue("V1", "error", rid, msg))

    for col in REQUIRED_COLUMNS:
        if col not in row:
            err(f"missing column {col}")
    if issues:
        return None, issues
    original = row["original_id"].strip()
    substitute = row["substitute_id"].strip()
    if not original or not substitute:
        err("original_id and substitute_id are required")
    basis = row["ratio_basis"].strip()
    if basis not in RATIO_BASES:
        err(f"ratio_basis {basis!r} not in {sorted(RATIO_BASES)}")
    context = row["context"].strip()
    if context not in CONTEXTS:
        err(f"context {context!r} not in {sorted(CONTEXTS)}")
    ratio: float | None = None
    if (row["ratio"] or "").strip():
        try:
            ratio = float(row["ratio"])
        except ValueError:
            err(f"ratio {row['ratio']!r} is not a number")
    try:
        confidence = int(row["confidence"])
    except (TypeError, ValueError):
        err(f"confidence {row['confidence']!r} is not an integer")
        confidence = 0
    if issues:
        return None, issues
    return Rule(rid, original, substitute, ratio, basis, context, _split_tags(row["diet_tags"]),
                confidence, (row["notes"] or "").strip()), issues


def validate(rows: Iterable[Mapping[str, str]], categories: Mapping[str, str],
             recipe_counts: Mapping[str, int] | None = None) -> tuple[list[Rule], list[Issue]]:
    """Run V1–V10. `categories` = ingredient_dim id -> category. Returns (parsed rules, issues).

    A rule set is publishable iff no issue has severity 'error' (ETL gate G8/G9).
    """
    rules: list[Rule] = []
    issues: list[Issue] = []
    seen_ids: set[str] = set()
    seen_keys: dict[tuple[str, str, str], str] = {}

    for row in rows:
        rule, row_issues = parse_row(row)
        issues += row_issues
        if rule is None:
            continue
        rid = rule.rule_id
        if rid in seen_ids:
            issues.append(Issue("V1", "error", rid, "duplicate rule_id"))
        seen_ids.add(rid)
        if rule.original_id == rule.substitute_id:
            issues.append(Issue("V2", "error", rid, "self-substitution"))
        key = (rule.original_id, rule.substitute_id, rule.context)
        if key in seen_keys:
            issues.append(Issue("V3", "error", rid, f"duplicate pair+context of {seen_keys[key]}"))
        seen_keys.setdefault(key, rid)
        for iid in (rule.original_id, rule.substitute_id):
            if iid not in categories:
                issues.append(Issue("V4", "error", rid, f"{iid!r} not in ingredient_dim"))
        if rule.ratio_basis == "same_unit" and not (rule.ratio is not None and 0 < rule.ratio <= 10):
            issues.append(Issue("V5", "error", rid, "same_unit needs 0 < ratio <= 10"))
        if rule.ratio_basis == "note" and (rule.ratio is not None or not rule.notes):
            issues.append(Issue("V5", "error", rid, "note basis needs empty ratio and non-empty notes"))
        if rule.confidence not in (1, 2, 3):
            issues.append(Issue("V6", "error", rid, "confidence must be 1, 2 or 3"))
        unknown = rule.diet_tags - DIET_TAGS
        if unknown:
            issues.append(Issue("V7", "error", rid, f"unknown diet tags {sorted(unknown)}"))
        forbidden = FORBIDDEN_TAGS_BY_CATEGORY.get(categories.get(rule.substitute_id, ""), frozenset())
        clash = rule.diet_tags & forbidden
        if clash:
            issues.append(Issue("V8", "error", rid,
                                f"{rule.substitute_id} is {categories[rule.substitute_id]} "
                                f"but tagged {sorted(clash)}"))
        rules.append(rule)

    # V9 reciprocal sanity (warning)
    by_pair = {(r.original_id, r.substitute_id, r.context): r for r in rules}
    for (a, b, ctx), r in by_pair.items():
        back = by_pair.get((b, a, ctx))
        if back and a < b and r.ratio and back.ratio:
            product = r.ratio * back.ratio
            if not 0.75 <= product <= 1.25:
                issues.append(Issue("V9", "warning", r.rule_id,
                                    f"{a}->{b} x {b}->{a} ratios multiply to {product:.2f}"))

    # V10 coverage report (info)
    if recipe_counts is not None:
        originals = {r.original_id for r in rules}
        for iid in sorted(originals):
            if recipe_counts.get(iid, 0) == 0:
                issues.append(Issue("V10", "info", "-", f"rule for {iid!r} but it appears in no recipe"))
        top = sorted(recipe_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:50]
        for iid, n in top:
            if iid not in originals:
                issues.append(Issue("V10", "info", "-", f"top ingredient {iid!r} ({n} recipes) has no rule"))
    return rules, issues


def has_errors(issues: Iterable[Issue]) -> bool:
    return any(i.severity == "error" for i in issues)


# ---------------------------------------------------------------------------------------------
# Lookup (docs/07 §4)
# ---------------------------------------------------------------------------------------------

def lookup(ruleset: RuleSet, ingredient_id: str, pantry: frozenset[str] = frozenset(),
           diet: frozenset[str] = frozenset(), context: str | None = None,
           amount: float | None = None, unit: str | None = None) -> list[SubstituteHit]:
    """Single-hop substitutes for one ingredient, in a reproducible total order."""
    hits: list[SubstituteHit] = []
    for rule in ruleset.by_original.get(ingredient_id, ()):
        if context and rule.context not in (context, "any"):
            continue
        if not diet <= rule.diet_tags:
            continue
        if rule.substitute_id == ingredient_id:        # defence in depth (V2)
            continue
        scaled = None
        if amount is not None and rule.ratio_basis == "same_unit" and rule.ratio is not None:
            scaled = round(amount * rule.ratio, 3)
        scaled_unit = unit if scaled is not None else None
        hits.append(SubstituteHit(rule, rule.substitute_id in pantry, scaled, scaled_unit))

    def order(h: SubstituteHit) -> tuple[int, int, int, str]:
        exact_ctx = 0 if (context and h.rule.context == context) else 1
        return (0 if h.in_pantry else 1, -h.rule.confidence, exact_ctx, h.rule.rule_id)

    hits.sort(key=order)
    return hits
