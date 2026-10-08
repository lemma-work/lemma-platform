"""Settings for the root key, beyond the ones ``app/core/config.py`` already has.

``SECRET_KEY_PROVIDER``, ``SECRET_ENCRYPTION_KEY(SET)``, ``GCP_KMS_KEY_NAME``
and ``GCP_SECRET_MANAGER_SECRET_NAME`` stay where they are; this holds only what
the vault's root providers added.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.settings_env import dotenv_path


class CryptoSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=dotenv_path(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    gcp_kms_key_version: str | None = Field(
        default=None,
        description=(
            "Pin wraps to one Cloud KMS key version "
            "(…/cryptoKeys/<key>/cryptoKeyVersions/<n>). Unset: the key's "
            "primary version, so KMS-side rotation takes effect by itself."
        ),
    )
    gcp_kms_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        description="Deadline for one Cloud KMS call, per attempt.",
    )
    gcp_kms_max_attempts: int = Field(
        default=4,
        ge=1,
        description="Attempts for a Cloud KMS call that fails transiently.",
    )


crypto_settings = CryptoSettings()
