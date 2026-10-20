"""Ingredient-line parsing and canonicalisation (docs/05 §4). Fixes F-003, F-004, F-005.

The same functions run inside Spark UDFs (ETL) and inside FastAPI (pantry / substitute input),
so `tomatoes` means the same thing everywhere.

Pipeline for one raw line:
    expand unicode fractions -> pull out parentheticals / optional markers -> leading quantity
    -> next WHOLE token as unit -> clean name  (parse_line)
    -> alias lookup -> strip descriptors + singularise -> alias lookup  (Canonicalizer.resolve)
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from fractions import Fraction

# ---------------------------------------------------------------------------------------------
# Units: 27 canonical slugs (docs/05 §4.2) and every surface form we accept for them.
# ---------------------------------------------------------------------------------------------

CANONICAL_UNITS: tuple[str, ...] = (
    "teaspoon", "tablespoon", "cup", "fluid_ounce", "pint", "quart", "gallon", "milliliter", "liter",
    "gram", "kilogram", "ounce", "pound", "pinch", "dash", "clove", "slice", "piece", "can", "package",
    "stick", "bunch", "sprig", "head", "stalk", "item",
)

_UNIT_FORMS: dict[str, tuple[str, ...]] = {
    "teaspoon": ("teaspoon", "teaspoons", "tsp", "tsps", "t"),
    "tablespoon": ("tablespoon", "tablespoons", "tbsp", "tbsps", "tbs", "tbl", "tbls", "T"),
    "cup": ("cup", "cups", "c"),
    "fluid_ounce": ("fl oz", "fluid ounce", "fluid ounces", "floz"),
    "pint": ("pint", "pints", "pt", "pts"),
    "quart": ("quart", "quarts", "qt", "qts"),
    "gallon": ("gallon", "gallons", "gal", "gals"),
    "milliliter": ("milliliter", "milliliters", "millilitre", "millilitres", "ml", "mls"),
    "liter": ("liter", "liters", "litre", "litres", "l"),
    "gram": ("gram", "grams", "gramme", "grammes", "g", "gr", "grs"),
    "kilogram": ("kilogram", "kilograms", "kg", "kgs", "kilo", "kilos"),
    "ounce": ("ounce", "ounces", "oz", "ozs"),
    "pound": ("pound", "pounds", "lb", "lbs"),
    "pinch": ("pinch", "pinches"),
    "dash": ("dash", "dashes"),
    "clove": ("clove", "cloves"),
    "slice": ("slice", "slices"),
    "piece": ("piece", "pieces", "pc", "pcs"),
    "can": ("can", "cans", "tin", "tins"),
    "package": ("package", "packages", "pkg", "pkgs", "packet", "packets"),
    "stick": ("stick", "sticks"),
    "bunch": ("bunch", "bunches"),
    "sprig": ("sprig", "sprigs"),
    "head": ("head", "heads"),
    "stalk": ("stalk", "stalks"),
}

# Case-sensitive forms ("T" = tablespoon vs "t" = teaspoon) are checked first, then lower-case.
_UNIT_EXACT: dict[str, str] = {f: u for u, forms in _UNIT_FORMS.items() for f in forms if f in ("T", "t")}
_UNIT_LOWER: dict[str, str] = {
    f.lower(): u for u, forms in _UNIT_FORMS.items() for f in forms if f not in ("T", "t")
}
_TWO_WORD_UNITS = {f for f in _UNIT_LOWER if " " in f}
# Single-letter units are only accepted right after a quantity ("2 g sugar", never "g sugar").
_SINGLE_LETTER = {"t", "c", "l", "g", "T"}

# Count nouns written after the name ("2 garlic cloves") are moved into the unit slot.
_TRAILING_COUNT_UNITS = {"clove": "clove", "cloves": "clove", "sprig": "sprig", "sprigs": "sprig",
                         "stalk": "stalk", "stalks": "stalk"}

# ---------------------------------------------------------------------------------------------
# Name cleaning vocabulary
# ---------------------------------------------------------------------------------------------

UNICODE_FRACTIONS: dict[str, str] = {
    "½": "1/2", "⅓": "1/3", "⅔": "2/3", "¼": "1/4", "¾": "3/4", "⅕": "1/5", "⅖": "2/5", "⅗": "3/5",
    "⅘": "4/5", "⅙": "1/6", "⅚": "5/6", "⅛": "1/8", "⅜": "3/8", "⅝": "5/8", "⅞": "7/8",
}

# Words that describe state/quality, not identity. Stripped only on the *retry* path of resolve().
DESCRIPTORS: frozenset[str] = frozenset({
    "fresh", "freshly", "organic", "extra", "virgin", "sifted", "chopped", "minced", "diced", "sliced",
    "grated", "shredded", "crushed", "finely", "coarsely", "roughly", "thinly", "large", "small", "medium",
    "softened", "melted", "cold", "warm", "hot", "room", "temperature", "unsalted", "salted", "boneless",
    "skinless", "ripe", "dried", "raw", "plain", "all-purpose", "peeled", "seeded", "pitted", "packed",
    "lightly", "beaten", "cubed", "halved", "quartered", "trimmed", "rinsed", "drained", "frozen",
    "thawed", "canned", "prepared", "cooked", "uncooked", "whole", "good", "quality", "pure", "leftover",
})

SINGULAR_EXCEPTIONS: frozenset[str] = frozenset({
    "hummus", "molasses", "couscous", "asparagus", "swiss", "grits", "oats", "citrus", "bass",
    "watercress", "hibiscus", "schnapps", "brussels", "series", "species", "glass", "lemongrass",
    "chives", "greens", "noodles", "sprouts", "bitters", "jus", "tapas", "pancreas", "octopus",
    "cress", "floss", "moss", "anise", "cheese", "sauce", "rice", "juice", "lettuce", "spice",
})
_IRREGULAR_PLURALS: dict[str, str] = {
    "leaves": "leaf", "halves": "half", "loaves": "loaf", "knives": "knife", "calves": "calf",
    "olives": "olive", "cloves": "clove", "chives": "chives", "potatoes": "potato",
    "tomatoes": "tomato", "mangoes": "mango", "avocados": "avocado", "anchovies": "anchovy",
}

_OPTIONAL_RE = re.compile(r"\(\s*optional\s*\)|,?\s*\boptional\b|,?\s*\bif desired\b|,?\s*\bfor garnish\b",
                          re.IGNORECASE)
_TO_TASTE_RE = re.compile(r",?\s*\b(to taste|as needed|as required|or to taste)\b", re.IGNORECASE)
_PAREN_RE = re.compile(r"\([^()]*\)|\[[^\[\]]*\]")
_NUM = r"(?:\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?|\.\d+)"
_QTY_RE = re.compile(rf"^\s*(?P<a>{_NUM})(?:\s*(?:-|to|or)\s*(?P<b>{_NUM}))?(?=\s|$|[a-zA-Z])")
_ARTICLE_RE = re.compile(r"^\s*(a|an|one)\s+", re.IGNORECASE)
_NON_NAME_CHARS = re.compile(r"[^a-z0-9' \-&]")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class ParsedLine:
    """Result of parse_line. `name` is cleaned but not yet canonical."""

    raw: str
    quantity: float | None
    unit: str
    name: str
    is_optional: bool
    parse_status: str  # ok | partial | unparsed


# ---------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------

def expand_unicode(text: str) -> str:
    """NFKC-normalise, expand vulgar fractions (1½ -> 1 1/2) and unify dashes."""
    out = []
    for ch in text:
        if ch in UNICODE_FRACTIONS:
            if out and out[-1].isdigit():
                out.append(" ")
            out.append(UNICODE_FRACTIONS[ch])
        else:
            out.append(ch)
    text = "".join(out).replace("⁄", "/")
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"[‒–—―−]", "-", text)


def _to_number(token: str) -> Fraction:
    parts = token.split()
    total = Fraction(0)
    for part in parts:
        total += Fraction(part)  # handles "1/2", "2", "0.5"; never eval()
    return total


def parse_quantity(text: str) -> tuple[float | None, str]:
    """Parse a leading quantity (mixed number, fraction, decimal, range -> midpoint).

    Returns (quantity or None, remaining text).
    """
    m = _QTY_RE.match(text)
    if not m:
        return None, text
    try:
        a = _to_number(m.group("a"))
        b = _to_number(m.group("b")) if m.group("b") else None
    except (ValueError, ZeroDivisionError):
        return None, text
    value = (a + b) / 2 if b is not None else a
    return float(value), text[m.end():]


def fold_accents(text: str) -> str:
    """'jalapeño' -> 'jalapeno', 'crème' -> 'creme' (so the ASCII-only name filter keeps the letter)."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def slugify(name: str) -> str:
    """'Extra-Virgin olive oil' -> 'extra_virgin_olive_oil' (ids must match ^[a-z0-9_]{1,64}$)."""
    return _SLUG_RE.sub("_", fold_accents(name.lower())).strip("_")[:64]


def singularise_word(word: str) -> str:
    """Conservative English singularisation with an exception list."""
    if word in SINGULAR_EXCEPTIONS or len(word) <= 3:
        return word
    if word in _IRREGULAR_PLURALS:
        return _IRREGULAR_PLURALS[word]
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("oes"):
        return word[:-2]
    if word.endswith(("ches", "shes", "sses", "xes", "zes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def singularise(name: str) -> str:
    """Singularise the head noun (last word): 'cherry tomatoes' -> 'cherry tomato'."""
    words = name.split()
    if not words:
        return name
    words[-1] = singularise_word(words[-1])
    return " ".join(words)


def clean_name(text: str) -> str:
    """Lower-case, drop parentheticals, post-comma prep notes, 'to taste', stray punctuation."""
    text = _PAREN_RE.sub(" ", text)
    text = _TO_TASTE_RE.sub("", text)
    text = text.split(",", 1)[0]
    text = fold_accents(text.lower().replace("’", "'"))
    text = _NON_NAME_CHARS.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" -'&")
    if text.startswith("of "):
        text = text[3:]
    return text.strip()


def _match_unit(rest: str, have_quantity: bool) -> tuple[str | None, str]:
    """Take the NEXT whole token(s) of `rest` as a unit candidate. No prefix matching (F-003)."""
    tokens = rest.split()
    if not tokens:
        return None, rest
    two = " ".join(tokens[:2]).lower().rstrip(".")
    if len(tokens) >= 2 and two in _TWO_WORD_UNITS:
        return _UNIT_LOWER[two], " ".join(tokens[2:])
    tok = tokens[0].rstrip(".")
    unit = _UNIT_EXACT.get(tok) or _UNIT_LOWER.get(tok.lower())
    if unit is None:
        return None, rest
    if (tok in _SINGLE_LETTER or tok.lower() in _SINGLE_LETTER) and not have_quantity:
        return None, rest
    remaining = " ".join(tokens[1:])
    if remaining.lower().startswith("of "):
        remaining = remaining[3:]
    return unit, remaining


# ---------------------------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------------------------

def parse_line(raw: str | None) -> ParsedLine:
    """Split one raw ingredient line into quantity / unit / cleaned name / optional flag.

    Quantity is None when the line has none (we do not invent 1.0 like the original).
    """
    raw = raw or ""
    text = expand_unicode(raw).strip()
    if not text:
        return ParsedLine(raw, None, "item", "", False, "unparsed")

    is_optional = bool(_OPTIONAL_RE.search(text))
    text = _OPTIONAL_RE.sub("", text)
    # Parentheticals such as "(14 oz)" sit between quantity and unit; drop them before unit detection.
    text = re.sub(r"\s+", " ", _PAREN_RE.sub(" ", text)).strip()

    quantity, rest = parse_quantity(text)
    if quantity is None:
        m = _ARTICLE_RE.match(text)
        if m:
            unit_probe, _ = _match_unit(text[m.end():], have_quantity=True)
            if unit_probe is not None:
                quantity, rest = 1.0, text[m.end():]
    unit, rest = _match_unit(rest, have_quantity=quantity is not None)
    name = clean_name(rest)

    if unit is None:
        unit = "item"
        words = name.split()
        if len(words) >= 2 and words[-1] in _TRAILING_COUNT_UNITS:
            unit = _TRAILING_COUNT_UNITS[words[-1]]
            name = " ".join(words[:-1])

    if not name:
        status = "unparsed"
    elif re.search(r"\d", name):
        status = "partial"
    else:
        status = "ok"
    return ParsedLine(raw, quantity, unit, name, is_optional, status)


def strip_descriptors(name: str) -> str:
    kept = [w for w in name.split() if w not in DESCRIPTORS]
    return " ".join(kept) if kept else name


def normalise_key(name: str) -> str:
    """Key used for alias lookups: cleaned, single-spaced, lower-case."""
    return clean_name(expand_unicode(name))


class Canonicalizer:
    """Maps a cleaned ingredient name to a canonical ingredient_id (docs/05 §4.1, §4.3).

    alias_to_id : normalised surface form -> ingredient_id   (config/ingredient_aliases.csv + auto-promoted)
    id_to_name  : ingredient_id -> canonical display name   (ingredient_dim)
    """

    def __init__(self, alias_to_id: dict[str, str], id_to_name: dict[str, str]):
        self._alias = alias_to_id
        self._names = id_to_name

    def _lookup(self, key: str) -> str | None:
        if not key:
            return None
        hit = self._alias.get(key)
        if hit:
            return hit
        slug = slugify(key)
        return slug if slug in self._names else None

    def resolve(self, name: str) -> tuple[str | None, str]:
        """Return (ingredient_id or None, display name)."""
        key = normalise_key(name)
        candidates = [key, singularise(key)]
        stripped = strip_descriptors(key)
        candidates += [stripped, singularise(stripped)]
        for cand in candidates:
            iid = self._lookup(cand)
            if iid:
                return iid, self._names.get(iid, cand)
        return None, singularise(stripped)

    def resolve_line(self, raw: str) -> tuple[ParsedLine, str | None, str]:
        parsed = parse_line(raw)
        iid, display = self.resolve(parsed.name) if parsed.name else (None, "")
        return parsed, iid, display


def build_alias_map(rows: Iterable[Mapping[str, str]], known_ids: Iterable[str] = ()) -> dict[str, str]:
    """alias CSV rows -> {normalised alias: ingredient_id}. Canonical names map to themselves too.

    Keys go through normalise_key so a lookup of any user spelling hits the same entry.
    """
    out = {normalise_key(i.replace("_", " ")): i for i in known_ids}
    for row in rows:
        key = normalise_key(row["alias"])
        if key:
            out[key] = row["ingredient_id"].strip()
    return out
