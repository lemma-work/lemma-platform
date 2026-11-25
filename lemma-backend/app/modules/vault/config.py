"""Vault module settings. Root-key settings live in ``app/core/crypto/config.py``."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.settings_env import dotenv_path


class VaultSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=dotenv_path(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    vault_kek_refresh_seconds: float = Field(
        default=300.0,
        gt=0,
        description=(
            "How often a process re-reads vault_keys, so a KEK rotated elsewhere "
            "is used for new writes without a restart."
        ),
    )
    vault_rewrap_batch_size: int = Field(
        default=500,
        ge=1,
        description="Secrets per transaction when moving data keys to a new KEK.",
    )


vault_settings = VaultSettings()
