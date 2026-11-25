"""A Google provider through the API: what is saved, shown and refused.

Everything runs for real -- the route, the profile, encryption, the repository
-- except Google's own answer to "may these credentials use this model", which
is the one network call a provider makes before it is saved.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette import status

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.domain.runtime_profiles import (
    RuntimeModelCapability,
    ServiceAccountRuntimeCredentials,
)
from app.modules.agent.infrastructure.providers import google_genai_model
from app.modules.agent.infrastructure.providers.discovery import (
    ProviderKeyRejectedError,
)
from app.modules.agent.infrastructure.repositories import (
    AgentRuntimeProfileRepository,
)

pytestmark = pytest.mark.e2e


def _key_file() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return json.dumps(
        {
            "type": "service_account",
            "project_id": "key-file-project",
            "private_key_id": "k1",
            "private_key": key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ).decode(),
            "client_email": "runner@key-file-project.iam.gserviceaccount.com",
            "client_id": "1",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )


class _Google:
    """Google's answer to a model lookup, and what it was asked."""

    def __init__(self, *, accepts: bool = True) -> None:
        self.accepts = accepts
        self.asked: list[tuple[str, str | None, str]] = []

    async def __call__(
        self,
        *,
        config: google_genai_model.GoogleGenAIRuntimeConfig,
        credentials: Mapping[str, object] | None,
        scope: object,
        model_name: str,
    ) -> None:
        self.asked.append((config.backend.value, config.vertex_project, model_name))
        assert scope != "SYSTEM"  # an organization's provider is never the server
        assert credentials
        if not self.accepts:
            raise ProviderKeyRejectedError("403")


@pytest.fixture
def google(monkeypatch: pytest.MonkeyPatch) -> _Google:
    answer = _Google()
    monkeypatch.setattr(google_genai_model, "verify_google_genai_access", answer)
    return answer


def _profiles(org: Mapping[str, object]) -> str:
    return f"/organizations/{org['id']}/agent-runtime/profiles"


@pytest.mark.asyncio
async def test_a_vertex_provider_is_saved_with_its_key_file_and_never_shows_it(
    authenticated_client, fixed_test_org, google: _Google
) -> None:
    key_file = _key_file()

    created = await authenticated_client.post(
        _profiles(fixed_test_org),
        json={
            "source": "GOOGLE_GENAI",
            "name": f"Vertex {uuid4().hex[:8]}",
            "backend": "VERTEX_AI",
            "service_account_json": key_file,
        },
    )

    assert created.status_code == status.HTTP_201_CREATED, created.text
    body = created.json()
    assert "PRIVATE KEY" not in created.text
    assert body["protocol"] == "GOOGLE_GENAI"
    assert body["has_credentials"] is True
    # The key file's own project, since none was named.
    assert body["config"]["vertex_project"] == "key-file-project"
    assert body["default_model_name"] == "gemini-4-hydrogen"
    image = next(
        m for m in body["model_catalog"] if m["name"] == "gemini-3.1-flash-image"
    )
    assert RuntimeModelCapability.IMAGE_OUTPUT.value in image["capabilities"]
    assert RuntimeModelCapability.TOOLS.value not in image["capabilities"]
    assert google.asked == [("VERTEX_AI", "key-file-project", "gemini-4-hydrogen")]

    async with create_uow_from_session_maker(async_session_maker) as uow:
        stored = await AgentRuntimeProfileRepository(uow).get_visible_by_id(
            profile_id=body["id"],
            organization_id=UUID(str(fixed_test_org["id"])),
            user_id=uuid4(),
        )
    assert stored is not None
    assert isinstance(stored.credentials, ServiceAccountRuntimeCredentials)
    assert stored.credentials.service_account_json.get_secret_value() == key_file


@pytest.mark.asyncio
async def test_a_rename_keeps_the_key_file_and_asks_google_nothing(
    authenticated_client, fixed_test_org, google: _Google
) -> None:
    created = await authenticated_client.post(
        _profiles(fixed_test_org),
        json={
            "source": "GOOGLE_GENAI",
            "name": f"Vertex {uuid4().hex[:8]}",
            "backend": "VERTEX_AI",
            "service_account_json": _key_file(),
        },
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    google.asked.clear()

    renamed = await authenticated_client.patch(
        f"{_profiles(fixed_test_org)}/{created.json()['id']}",
        json={"source": "GOOGLE_GENAI", "name": "Renamed"},
    )
    drawing_default = await authenticated_client.patch(
        f"{_profiles(fixed_test_org)}/{created.json()['id']}",
        json={"source": "GOOGLE_GENAI", "default_model_name": "gemini-3.1-flash-image"},
    )

    assert renamed.status_code == status.HTTP_200_OK, renamed.text
    assert renamed.json()["has_credentials"] is True
    assert google.asked == []
    # An image model cannot run a Lemma, so it cannot be the default.
    assert drawing_default.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "reason"),
    [
        pytest.param(
            {"backend": "VERTEX_AI"},
            "service account key file",
            id="vertex-no-key-file",
        ),
        pytest.param(
            {"backend": "VERTEX_AI", "api_key": "AIza-e2e"},
            "not an API key",
            id="vertex-with-a-key",
        ),
        pytest.param({"backend": "GEMINI_API"}, "needs an API key", id="gemini-no-key"),
    ],
)
async def test_a_google_provider_without_its_own_way_in_is_refused(
    authenticated_client,
    fixed_test_org,
    google: _Google,
    body: dict[str, object],
    reason: str,
) -> None:
    refused = await authenticated_client.post(
        _profiles(fixed_test_org),
        json={"source": "GOOGLE_GENAI", "name": f"G {uuid4().hex[:8]}", **body},
    )

    assert refused.status_code == status.HTTP_400_BAD_REQUEST, refused.text
    assert reason in refused.json()["message"]
    assert google.asked == []


@pytest.mark.asyncio
async def test_credentials_google_refuses_are_not_saved(
    authenticated_client, fixed_test_org, google: _Google
) -> None:
    google.accepts = False
    name = f"Refused {uuid4().hex[:8]}"

    refused = await authenticated_client.post(
        _profiles(fixed_test_org),
        json={
            "source": "GOOGLE_GENAI",
            "name": name,
            "backend": "GEMINI_API",
            "api_key": "AIza-e2e-not-real",
        },
    )
    listed = await authenticated_client.get(_profiles(fixed_test_org))

    assert refused.status_code == status.HTTP_400_BAD_REQUEST
    assert "rejected" in refused.json()["message"]
    assert "AIza-e2e-not-real" not in refused.text
    assert name not in listed.text
