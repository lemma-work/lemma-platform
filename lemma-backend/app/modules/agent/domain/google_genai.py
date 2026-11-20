"""A Google Gen AI provider's own shapes: which host it reaches, and how it
signs in there.

One wire, two hosts. The Gemini API takes an API key; Vertex AI takes a
service account -- a stored key file, or for the deployment's own profile the
identity the server already runs as.
"""

from __future__ import annotations

import json
from enum import Enum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    SecretStr,
    field_validator,
    model_validator,
)

from app.modules.agent.domain.value_objects import JsonObject


class ServiceAccountRuntimeCredentials(BaseModel):
    """A Google Cloud service account's JSON key, for Vertex AI.

    Held whole and secret: the file carries the private key, and nothing in
    it is shown back. Read only through ``reveal_credentials``.
    """

    service_account_json: SecretStr = Field(min_length=1)

    @field_validator("service_account_json")
    @classmethod
    def is_a_service_account_key(cls, value: SecretStr) -> SecretStr:
        # Checked here so a pasted wrong file fails the save with a reason,
        # not every later run with a provider error. Nothing of the value is
        # quoted back.
        try:
            info = json.loads(value.get_secret_value())
        except ValueError:
            raise ValueError("is not JSON") from None
        if not isinstance(info, dict) or info.get("type") != "service_account":
            raise ValueError("is not a Google Cloud service account key")
        for key in ("client_email", "private_key", "project_id"):
            if not isinstance(info.get(key), str) or not info[key]:
                raise ValueError(f"has no {key}")
        return value


class GoogleGenAIBackend(str, Enum):
    """Which host answers the Google Gen AI API, and so how it is signed in to.

    ``GEMINI_API`` takes an API key. ``VERTEX_AI`` takes a service account --
    a stored key file, or for the deployment's own profile, the identity the
    server already runs as (Application Default Credentials).
    """

    GEMINI_API = "GEMINI_API"
    VERTEX_AI = "VERTEX_AI"


#: Vertex AI's location when none is given: the multi-region endpoint that
#: serves every current Gemini model.
DEFAULT_VERTEX_LOCATION = "global"


class GoogleGenAIRuntimeConfig(BaseModel):
    # Every field has a default, so without this another protocol's config
    # would read as a Gemini API one -- the re-typing `config_for_protocol`
    # exists to refuse.
    model_config = ConfigDict(extra="forbid")

    backend: GoogleGenAIBackend = GoogleGenAIBackend.GEMINI_API
    #: Vertex AI only: the project billed, and where requests go.
    vertex_project: str | None = None
    vertex_location: str = DEFAULT_VERTEX_LOCATION
    #: Gemini API only: a gateway in front of it. Empty is Google's own host.
    base_url: HttpUrl | None = None
    model_settings: JsonObject = Field(default_factory=dict)

    @field_validator("vertex_project", "vertex_location", mode="before")
    @classmethod
    def strip_names(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @model_validator(mode="after")
    def backend_has_what_it_needs(self) -> "GoogleGenAIRuntimeConfig":
        if self.backend is GoogleGenAIBackend.VERTEX_AI:
            if not self.vertex_project:
                raise ValueError("Vertex AI needs vertex_project")
            if not self.vertex_location:
                self.vertex_location = DEFAULT_VERTEX_LOCATION
        return self
