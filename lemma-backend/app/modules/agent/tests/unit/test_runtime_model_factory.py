from pydantic_ai.models.openai import OpenAIChatModel

from app.modules.agent.services.runtime_model_factory import (
    pydantic_ai_model_from_runtime_profile,
)


def test_openai_provider_uses_runtime_type_discriminator():
    model = pydantic_ai_model_from_runtime_profile(
        runtime_profile={
            "profile_id": "provider-1",
            "runtime_type": "OPENAI_COMPATIBLE",
            "provider_model_name": "provider/model-a",
            "config": {
                "base_url": "https://provider.test/v1",
                "headers": {},
            },
        },
        runtime_credentials={"api_key": "secret"},
    )

    assert isinstance(model, OpenAIChatModel)


def test_removed_protocol_field_does_not_select_a_provider():
    model = pydantic_ai_model_from_runtime_profile(
        runtime_profile={
            "profile_id": "legacy-provider",
            "protocol": "OPENAI_COMPATIBLE",
            "provider_model_name": "provider/model-a",
            "config": {"base_url": "https://provider.test/v1"},
        },
        runtime_credentials={"api_key": "secret"},
    )

    assert model is None
