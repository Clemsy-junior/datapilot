"""Application settings.

Every knob is an environment variable so that the same image can run in a dev
container, in CI and in production without a rebuild. Nothing here has a secret
default: the app must be fully usable with `DATAPILOT_LLM_PROVIDER=fake`, which
is what makes the offline demo and the secret-free CI possible.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent

VERSION = "0.1.0"


class Settings(BaseSettings):
    """Runtime configuration, read from the environment (prefix ``DATAPILOT_``)."""

    model_config = SettingsConfigDict(
        env_prefix="DATAPILOT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM provider ------------------------------------------------------
    llm_provider: Literal["fake", "anthropic"] = "fake"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"
    anthropic_base_url: str = "https://api.anthropic.com"
    llm_max_tokens: int = Field(default=2048, ge=256, le=8192)

    # --- Agent loop guardrails --------------------------------------------
    max_iterations: int = Field(default=8, ge=1, le=32)
    request_timeout_seconds: float = Field(default=60.0, gt=0)
    max_tool_rows: int = Field(default=50, ge=1, le=1000)

    # --- Datasets ----------------------------------------------------------
    data_dir: Path = Path("data")
    max_upload_mb: int = Field(default=25, ge=1, le=500)

    # --- HTTP --------------------------------------------------------------
    # NoDecode: pydantic-settings tries to json.loads() any environment value
    # bound to a "complex" field (list, dict, ...) before validators ever run.
    # DATAPILOT_CORS_ORIGINS is a plain comma-separated string ("a,b"), not
    # JSON, so that implicit decode raised SettingsError before _split_origins
    # got a chance to run — invisible in tests that build Settings(...) with a
    # Python list directly, only surfacing when the value actually comes from
    # an environment variable (docker compose, the devcontainer, a real .env).
    # NoDecode disables that json.loads attempt so the raw string reaches the
    # validator below.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept a comma-separated string, because that is what Compose passes."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def resolved_data_dir(self) -> Path:
        """Absolute dataset directory; relative paths are anchored to ``backend/``."""
        if self.data_dir.is_absolute():
            return self.data_dir
        return BACKEND_ROOT / self.data_dir

    @property
    def max_upload_bytes(self) -> int:
        """Upload cap in bytes, so the request handler can compare directly."""
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached because settings are immutable for the lifetime of the process and
    reading the environment on every request would be wasteful. Tests clear the
    cache when they need a different configuration.
    """
    return Settings()
