"""How much one hook -- an address a service outside the pod posts to -- may cost it.

Kept apart from ``DatastoreSettings`` (``config.py`` is at the size ratchet);
the environment variables carry the same ``DATASTORE_`` prefix.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.settings_env import dotenv_path


class HookSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=dotenv_path(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # The body lands in one JSON cell, so its cap sits under
    # ``datastore_cell_max_bytes``.
    datastore_hook_body_max_bytes: int = Field(
        default=128 * 1024,
        ge=1024,
        # Not above 256 KB, the default ``datastore_cell_max_bytes``: past it
        # the record validator would refuse the payload with a vague 422
        # instead of this cap's 413, which names the limit.
        le=256 * 1024,
        description=(
            "Largest body (bytes) one delivery to a hook may carry. Env: "
            "``DATASTORE_HOOK_BODY_MAX_BYTES``."
        ),
    )
    datastore_hook_deliveries_per_minute: int = Field(
        default=60,
        ge=1,
        description=(
            "Deliveries one hook accepts a minute; past it the sender is told "
            "429 and retries. Env: ``DATASTORE_HOOK_DELIVERIES_PER_MINUTE``."
        ),
    )
    datastore_hook_deliveries_per_day: int = Field(
        default=5000,
        ge=1,
        description=(
            "Deliveries one hook accepts a day, so a sender stuck in a loop "
            "cannot fill a table. Env: ``DATASTORE_HOOK_DELIVERIES_PER_DAY``."
        ),
    )


hook_settings = HookSettings()
