"""Application configuration using pydantic-settings."""

from functools import lru_cache
from typing import Any

from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, EnvSettingsSource, SettingsConfigDict
from pydantic_settings.main import PydanticBaseSettingsSource

_DEFAULT_CORS_ORIGINS: list[str] = ["http://localhost:5173"]

_CORS_FIELD_NAME: str = "cors_origins"


def _parse_cors_string(raw: str) -> list[str] | None:
    """Convert a raw CORS string to a list of origin strings.

    Rules applied in order:

    - Empty or whitespace-only → returns the default list.
    - JSON array (starts with ``[``) → returns ``None`` to let pydantic-settings
      handle JSON parsing normally.
    - CSV (``"a,b"``) → split on comma, strip whitespace, drop empty tokens.
    """
    stripped: str = raw.strip()
    if not stripped:
        return list(_DEFAULT_CORS_ORIGINS)
    if stripped.startswith("["):
        return None
    return [item.strip() for item in stripped.split(",") if item.strip()]


class _CorsAwareEnvSource(EnvSettingsSource):
    """Custom env source that tolerates empty strings and CSV for cors_origins."""

    def prepare_field_value(
        self,
        field_name: str,
        field: FieldInfo,
        value: Any,
        value_is_complex: bool,
    ) -> Any:
        """Pre-process ``cors_origins`` before the standard JSON decoder runs.

        For every other field the default behaviour is preserved.
        """
        if field_name != _CORS_FIELD_NAME or not isinstance(value, str):
            return super().prepare_field_value(field_name, field, value, value_is_complex)

        result: list[str] | None = _parse_cors_string(value)
        if result is not None:
            return result

        return super().prepare_field_value(field_name, field, value, value_is_complex)


class Settings(BaseSettings):
    """Application settings loaded from environment variables or .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    api_port: int = 8787
    cors_origins: list[str] = _DEFAULT_CORS_ORIGINS
    claude_projects_root: str = "~/.claude/projects"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Replace the standard env source with the CORS-aware variant."""
        cors_env_source = _CorsAwareEnvSource(settings_cls)
        return (init_settings, cors_env_source, dotenv_settings, file_secret_settings)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached application settings instance."""
    return Settings()
