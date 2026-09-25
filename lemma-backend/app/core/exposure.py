"""Who can reach this deployment, beyond what its environment says.

``ENVIRONMENT=local`` answers what kind of deployment this is -- which storage,
which embeddings, which keys -- and a Lemma Desktop installation stays ``local``
when its owner shares it on the local network or through a public tunnel. What
*should* change then is everything local mode permits only because nobody else
is on the other end. Desktop says so with ``INSTALLATION_SHARED``, and the code
that relaxes something for local mode asks ``local_relaxations_allowed()``
rather than ``is_local_mode()``.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config import settings
from app.core.settings_env import dotenv_path


class ExposureSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=dotenv_path(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    installation_shared: bool = Field(
        default=False,
        description=(
            "A local installation that people other than its own user can "
            "reach. Lemma Desktop sets it while sharing on the local network or "
            "through a public tunnel. It turns off model providers on loopback, "
            "the loopback CORS defaults, the configuration block on /health "
            "and honouring SURFACE_WEBHOOK_SECURITY_ENABLED=false."
        ),
    )


exposure_settings = ExposureSettings()


def local_relaxations_allowed() -> bool:
    """Local mode, on a machine only its own user can reach."""
    return settings.is_local_mode() and not exposure_settings.installation_shared
