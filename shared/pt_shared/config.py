"""Centralised configuration read from environment variables.

Nothing here has a real secret baked in. Every secret (Devin API key, GitHub
token) is read from the environment; the defaults are safe placeholders used
only so the services can boot in demo mode without real credentials.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration shared by all three services."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Shared datastore -------------------------------------------------
    # Defaults to a SQLite file on a shared volume; docker-compose overrides
    # this with a Postgres URL so all three containers share one DB safely.
    database_url: str = Field(
        default="sqlite:////data/purple_team.db",
        alias="DATABASE_URL",
    )

    # --- Secrets (never hardcoded, always from env) -----------------------
    devin_api_key: str = Field(default="", alias="DEVIN_API_KEY")
    devin_api_base: str = Field(
        default="https://api.devin.ai", alias="DEVIN_API_BASE"
    )
    github_token: str = Field(default="", alias="GITHUB_TOKEN")
    issue_assignee: str = Field(
        default="", alias="GITHUB_ISSUE_ASSIGNEE"
    )

    # --- Target fork ------------------------------------------------------
    superset_fork_repo: str = Field(
        default="jaeiku/superset", alias="SUPERSET_FORK_REPO"
    )
    github_api_base: str = Field(
        default="https://api.github.com", alias="GITHUB_API_BASE"
    )

    # --- Guardrails -------------------------------------------------------
    max_acu_per_session: float = Field(default=10.0, alias="MAX_ACU_PER_SESSION")
    max_concurrent_sessions: int = Field(
        default=2, alias="MAX_CONCURRENT_SESSIONS"
    )
    global_budget_acu_ceiling: float = Field(
        default=100.0, alias="GLOBAL_BUDGET_ACU_CEILING"
    )

    # --- Operational mode -------------------------------------------------
    # When true, no real network calls are made to GitHub / Devin; the
    # services simulate the side effects and record them to the store. This is
    # what powers the `simulate` demo walkthrough.
    demo_mode: bool = Field(default=True, alias="DEMO_MODE")

    # Service URLs used for inter-service calls in docker-compose.
    blue_team_url: str = Field(
        default="http://blue-team:8000", alias="BLUE_TEAM_URL"
    )
    red_team_public_url: str = Field(
        default="", alias="RED_TEAM_PUBLIC_URL"
    )
    dashboard_public_url: str = Field(
        default="", alias="DASHBOARD_PUBLIC_URL"
    )
    red_team_port: int = Field(default=8001, alias="RED_TEAM_PORT")
    dashboard_port: int = Field(default=8003, alias="DASHBOARD_PORT")

    @property
    def superset_owner(self) -> str:
        return self.superset_fork_repo.split("/", 1)[0]

    @property
    def effective_issue_assignee(self) -> str:
        return self.issue_assignee or self.superset_owner

    @property
    def superset_name(self) -> str:
        return self.superset_fork_repo.split("/", 1)[1]


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""

    return Settings()
