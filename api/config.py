"""API settings from the environment (docs/10 §3). No hosts, ports or secrets in code (F-023)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    profile: str = "lite"                          # lite | standard
    snapshot_root: str = str(REPO_ROOT / "data" / "warehouse")

    # standard profile (M2)
    solr_url: str = "http://localhost:8983/solr"
    solr_alias: str = "recipes"
    solr_timeout_s: float = 3.0
    trino_host: str = "localhost"
    trino_port: int = 8080
    trino_catalog: str = "hive"
    trino_schema: str = "recipe_db"
    trino_pool_size: int = 8
    trino_timeout_s: float = 5.0

    # behaviour (docs/06, docs/08)
    staples: str = "salt,water,black_pepper"
    min_recipe_size: int = 3
    max_pantry_items: int = 30
    cache_reload_s: float = 60.0
    cors_origins: str = ""                         # comma-separated explicit origins; empty = none
    log_level: str = "INFO"

    @field_validator("profile")
    @classmethod
    def _profile(cls, v: str) -> str:
        if v not in ("lite", "standard"):
            raise ValueError("PROFILE must be lite or standard")
        return v

    @property
    def staples_set(self) -> frozenset[str]:
        return frozenset(s.strip() for s in self.staples.split(",") if s.strip())

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
