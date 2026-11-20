"""A GOOGLE_GENAI profile: how it signs in, what it offers, what it is sent.

The two hosts sign in differently -- the Gemini API with a key, Vertex AI as a
service account -- and the one fallback, the server's own Google identity, is
the deployment's alone: an organization's provider never borrows it.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr, ValidationError

from app.core.domain.generation import GenerationSettings, ReasoningEffort
from app.modules.agent.domain.errors import RuntimeProfileValidationError
from app.modules.agent.domain.google_genai_catalog import google_genai_catalog
from app.modules.agent.domain.runtime_choice import choose_workspace_runtime
from app.modules.agent.domain.runtime_profiles import (
    GoogleGenAIBackend,
    GoogleGenAIRuntimeConfig,
    RuntimeModelCapability,
    RuntimeProfileProtocol,
    ServiceAccountRuntimeCredentials,
    checked_profile,
    public_provider_config,
)
from app.modules.agent.infrastructure.providers.google_genai_model import (
    GoogleCredentialsMissing,
    google_genai_model,
)
from app.modules.agent.services.generation_settings import wire_settings

pytestmark = pytest.mark.unit

VERTEX = GoogleGenAIRuntimeConfig(
    backend=GoogleGenAIBackend.VERTEX_AI, vertex_project="a-project"
)
GEMINI_API = GoogleGenAIRuntimeConfig(backend=GoogleGenAIBackend.GEMINI_API)


def _service_account_json() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return json.dumps(
        {
            "type": "service_account",
            "project_id": "a-project",
            "private_key_id": "k1",
            "private_key": pem,
            "client_email": "runner@a-project.iam.gserviceaccount.com",
            "client_id": "1",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )


def test_an_organizations_vertex_provider_never_runs_as_the_server() -> None:
    """No key file on an organization's profile is a refusal, not a quiet
    fall-through to the deployment's own Google identity and project."""
    with pytest.raises(GoogleCredentialsMissing):
        google_genai_model(
            "gemini-4-hydrogen",
            config=VERTEX,
            credentials=None,
            scope="ORGANIZATION",
            http_client=None,
        )


def test_the_deployment_may_run_vertex_as_its_own_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Application Default Credentials are only looked up when a request is
    # made; building the model must not need them, or a laptop without gcloud
    # could not even list its profiles.
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "a-project")
    model = google_genai_model(
        "gemini-4-hydrogen",
        config=VERTEX,
        credentials=None,
        scope="SYSTEM",
        http_client=None,
    )
    assert model.model_name == "gemini-4-hydrogen"
    assert model.system == "google-cloud"


def test_a_service_account_key_file_signs_in_to_vertex() -> None:
    model = google_genai_model(
        "gemini-3.8-flash",
        config=VERTEX,
        credentials={"service_account_json": _service_account_json()},
        scope="ORGANIZATION",
        http_client=None,
    )
    assert model.system == "google-cloud"


def test_the_gemini_api_needs_its_key() -> None:
    with pytest.raises(GoogleCredentialsMissing):
        google_genai_model(
            "gemini-4-hydrogen",
            config=GEMINI_API,
            credentials=None,
            scope="SYSTEM",
            http_client=None,
        )
    model = google_genai_model(
        "gemini-4-hydrogen",
        config=GEMINI_API,
        credentials={"api_key": "AIza-test"},
        scope="ORGANIZATION",
        http_client=None,
    )
    assert model.system == "google"


@pytest.mark.parametrize(
    ("document", "reason"),
    [
        ("not json", "is not JSON"),
        (json.dumps({"type": "authorized_user"}), "not a Google Cloud service account"),
        (json.dumps({"type": "service_account"}), "has no client_email"),
    ],
)
def test_a_wrong_key_file_is_refused_with_a_reason_and_never_quoted(
    document: str, reason: str
) -> None:
    with pytest.raises(ValidationError) as raised:
        ServiceAccountRuntimeCredentials(service_account_json=SecretStr(document))
    assert reason in str(raised.value)
    assert document not in str(raised.value)


def test_vertex_needs_a_project() -> None:
    with pytest.raises(ValidationError):
        GoogleGenAIRuntimeConfig(backend=GoogleGenAIBackend.VERTEX_AI)


def test_the_public_config_names_the_host_and_project() -> None:
    public = public_provider_config(VERTEX.model_dump(mode="json"))
    assert public["backend"] == "VERTEX_AI"
    assert public["vertex_project"] == "a-project"


@pytest.mark.parametrize(
    ("effort", "level"),
    [
        (ReasoningEffort.NONE, "MINIMAL"),
        (ReasoningEffort.LOW, "LOW"),
        (ReasoningEffort.HIGH, "HIGH"),
    ],
)
def test_reasoning_effort_is_sent_as_a_gemini_thinking_level(
    effort: ReasoningEffort, level: str
) -> None:
    wire = wire_settings(
        GenerationSettings(reasoning_effort=effort, max_output_tokens=256),
        protocol=RuntimeProfileProtocol.GOOGLE_GENAI,
        capabilities=[RuntimeModelCapability.REASONING],
    )
    assert wire == {
        "max_tokens": 256,
        "google_thinking_config": {"thinking_level": level},
    }


def test_image_models_draw_and_never_run_a_lemma() -> None:
    catalog = google_genai_catalog(
        ["gemini-4-hydrogen"], ["gemini-3.1-flash-image", "gemini-4-hydrogen"]
    )
    assert [entry.name for entry in catalog] == [
        "gemini-4-hydrogen",
        "gemini-3.1-flash-image",
    ]
    chat, image = catalog
    assert not chat.draws_only
    assert image.draws_only
    assert RuntimeModelCapability.TOOLS not in image.capabilities

    with pytest.raises(RuntimeProfileValidationError, match="chat model"):
        checked_profile(
            id=str(uuid4()),
            organization_id=uuid4(),
            scope="ORGANIZATION",
            kind="MODEL_PROVIDER",
            protocol="GOOGLE_GENAI",
            name="Google",
            default_model_name="gemini-3.1-flash-image",
            model_catalog=catalog,
            config=GEMINI_API,
        )


def test_a_background_pick_never_lands_on_an_image_model_and_drawing_does() -> None:
    """Image models come last in a catalog, but a vision pick that skipped the
    chat models would still have found one: it reads images too."""
    profile = checked_profile(
        id=str(uuid4()),
        organization_id=uuid4(),
        scope="ORGANIZATION",
        kind="MODEL_PROVIDER",
        protocol="GOOGLE_GENAI",
        name="Google",
        default_model_name="gemini-4-hydrogen",
        model_catalog=google_genai_catalog(
            ["gemini-4-hydrogen"], ["gemini-3.1-flash-image"]
        ),
        config=GEMINI_API,
    )

    looking = choose_workspace_runtime(
        [profile],
        pod_default=None,
        model_name="gemini-3.1-flash-image",
        require_vision=True,
    )
    drawing = choose_workspace_runtime(
        [profile],
        pod_default=None,
        model_name=None,
        require_vision=False,
        require_image_output=True,
    )

    assert looking is not None and looking.model_name == "gemini-4-hydrogen"
    assert drawing is not None and drawing.model_name == "gemini-3.1-flash-image"
