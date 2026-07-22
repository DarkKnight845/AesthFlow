"""
Centralized settings, loaded from environment variables / .env file.

Why this exists as its own module: every other module (nodes, sandbox,
persistence, api) needs config values. If each imports os.environ directly,
you end up with scattered, unvalidated env access and no single place to see
what the app actually requires to run. Importing `settings` from here instead
gives you validation at startup (pydantic will fail fast if a required var
is missing) rather than a cryptic KeyError three layers deep during a run.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config  = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM
    openai_api_key: str = ""

    # Search
    tavily_api_key: str = ""

    # Postgres
    database_url: str = ""
    database_url_sync: str = ""

    # Redis
    redis_url: str = ""

    # Sandbox
    sandbox_timeout_seconds: int = 30
    sandbox_memory_limit_mb: int = 512
    sandbox_docker_image: str = "python:3.11-slim"

    # Cost guardrails
    max_tokens_per_run: int = 50_000

    # App
    environment: str = "development"
    log_level: str = "INFO"


# Instantiated once, imported everywhere else as `from orchestrator.config import settings`
settings = Settings()
