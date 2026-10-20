"""Input validation rules (docs/08 §5). Pure functions; raise ValidationFailed (-> 422)."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from recipe_core.errors import ValidationFailed

_TOKEN_EXTRA = set(" '-.’_")          # '_' so canonical ids (olive_oil) are accepted too
CUISINE_RE = re.compile(r"^[a-z0-9_]{1,40}$")
RECIPE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def clean_token(raw: str, field: str) -> str:
    token = unicodedata.normalize("NFKC", raw).strip()
    if not 1 <= len(token) <= 64:
        raise ValidationFailed(f"{field}: each item must be 1-64 characters")
    if not all(ch.isalnum() or ch in _TOKEN_EXTRA for ch in token):
        raise ValidationFailed(
            f"{field}: only letters, digits, space, apostrophe, hyphen, dot and underscore are allowed")
    return token


def split_multi(values: Iterable[str] | None, field: str, max_items: int) -> list[str]:
    """Accept repeated params AND comma-separated values (F-011). De-duplicates case-insensitively."""
    out: list[str] = []
    seen: set[str] = set()
    for v in values or ():
        for part in v.split(","):
            if not part.strip():
                continue
            token = clean_token(part, field)
            if token.lower() not in seen:
                seen.add(token.lower())
                out.append(token)
    if len(out) > max_items:
        raise ValidationFailed(f"{field}: at most {max_items} items")
    return out


def check_cuisine(value: str) -> str:
    v = value.strip().lower()
    if not CUISINE_RE.match(v):
        raise ValidationFailed("cuisine: must be a slug like 'italian' ([a-z0-9_], max 40)")
    return v


def check_recipe_id(value: str) -> str:
    if not RECIPE_ID_RE.match(value):
        raise ValidationFailed("recipe_id: must be 32 lower-case hex characters")
    return value
