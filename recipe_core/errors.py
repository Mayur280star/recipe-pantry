"""Domain errors. Adapters raise these; api/main.py maps them to HTTP (docs/09 §8, docs/08 §4)."""


class RecipeError(Exception):
    """Base class for every error the platform raises on purpose."""


class ValidationFailed(RecipeError):
    """Client input is invalid -> 422 validation_error."""

    code = "validation_error"


class EmptyPantry(ValidationFailed):
    """No usable pantry ingredients were supplied (docs/06 §2 edge cases)."""


class OnlyStaples(ValidationFailed):
    """Pantry resolved to staples only, e.g. just salt and water (docs/06 §2)."""


class NotFound(RecipeError):
    """Unknown id -> 404 not_found."""

    code = "not_found"


class UpstreamUnavailable(RecipeError):
    """Solr / Trino refused or pool exhausted -> 503 upstream_unavailable."""

    code = "upstream_unavailable"


class UpstreamTimeout(RecipeError):
    """Solr / Trino did not answer in time -> 504 upstream_timeout."""

    code = "upstream_timeout"
