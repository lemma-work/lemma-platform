"""What a provider's `/models` listing is asked, and what is read out of it.

The payloads below are trimmed from real answers. Nebius Token Factory's is the
one that motivated most of this: it says which models read images only when
asked for `verbose=true`, it lists an embedding model beside the chat ones, and
its chat endpoint refuses an image for any model it lists as `text->text`.
"""

from __future__ import annotations

from app.modules.agent.domain.runtime_profiles import RuntimeModelCapability
from app.modules.agent.services.runtime_provider_discovery import (
    _openai_listing_request,
    _parse_openai_compatible_models,
    _provider_model_catalog,
)

NEBIUS_VERBOSE_LISTING = {
    "data": [
        {
            "id": "moonshotai/Kimi-K3",
            "context_length": 1024000,
            "architecture": {"modality": "text+image->text", "tokenizer": "Other"},
            "supported_features": ["tools", "reasoning"],
        },
        {
            "id": "openai/gpt-oss-120b",
            "context_length": 131072,
            "architecture": {"modality": "text->text", "tokenizer": "Other"},
        },
        {
            "id": "Qwen/Qwen3-Embedding-8B",
            "context_length": 40960,
            "architecture": {"modality": "text->embedding", "tokenizer": "Other"},
        },
        {
            "id": "Qwen/Qwen3.8-27B",
            "architecture": {"modality": "text+image->text", "tokenizer": "Other"},
        },
    ]
}


def _listing_url(base_url: str) -> str:
    return _openai_listing_request(base_url=base_url, api_key="k", headers={}).url


class TestNebiusIsAskedForModalities:
    def test_token_factory_is_asked_for_the_verbose_listing(self) -> None:
        assert (
            _listing_url("https://api.tokenfactory.nebius.com/v1")
            == "https://api.tokenfactory.nebius.com/v1/models?verbose=true"
        )

    def test_the_older_studio_address_is_asked_too(self) -> None:
        assert _listing_url("https://api.studio.nebius.com/v1/").endswith(
            "/models?verbose=true"
        )

    def test_other_providers_get_the_plain_listing(self) -> None:
        """A strict server may refuse a query parameter it does not know."""
        assert (
            _listing_url("https://api.fireworks.ai/inference/v1")
            == "https://api.fireworks.ai/inference/v1/models"
        )

    def test_a_lookalike_domain_is_not_nebius(self) -> None:
        assert _listing_url("https://api.notnebius.com/v1").endswith("/models")


class TestNebiusListingIsRead:
    def test_the_models_it_lists_as_reading_images_are_vision_models(self) -> None:
        models = {
            model.name: model.supports_vision
            for model in _parse_openai_compatible_models(NEBIUS_VERBOSE_LISTING)
        }

        assert models == {
            "moonshotai/Kimi-K3": True,
            "openai/gpt-oss-120b": False,
            "Qwen/Qwen3.8-27B": True,
        }

    def test_the_embedding_model_is_not_offered_as_a_chat_model(self) -> None:
        names = [m.name for m in _parse_openai_compatible_models(NEBIUS_VERBOSE_LISTING)]

        assert "Qwen/Qwen3-Embedding-8B" not in names

    def test_the_saved_catalog_marks_only_those_models_vision(self) -> None:
        catalog = _provider_model_catalog(
            discovered_models=_parse_openai_compatible_models(NEBIUS_VERBOSE_LISTING),
            fallback_model_names=[],
        )

        vision = [
            entry.name
            for entry in catalog
            if RuntimeModelCapability.VISION in entry.capabilities
        ]
        assert vision == ["moonshotai/Kimi-K3", "Qwen/Qwen3.8-27B"]


class TestModalityIsReadFromTheRightSide:
    def test_a_model_that_draws_images_does_not_read_them(self) -> None:
        (model,) = _parse_openai_compatible_models(
            {
                "data": [
                    {"id": "draws", "architecture": {"modality": "text->text+image"}}
                ]
            }
        )

        assert model.supports_vision is False

    def test_a_model_that_only_outputs_images_is_not_a_chat_model(self) -> None:
        listing = {
            "data": [
                {"id": "painter", "architecture": {"modality": "text->image"}},
                {
                    "id": "painter-2",
                    "architecture": {
                        "input_modalities": ["text"],
                        "output_modalities": ["image"],
                    },
                },
            ]
        }

        assert _parse_openai_compatible_models(listing) == []

    def test_a_model_that_says_nothing_is_still_offered(self) -> None:
        """The standard OpenAI schema has no modalities; silence hides nothing."""
        (model,) = _parse_openai_compatible_models({"data": [{"id": "plain"}]})

        assert (model.name, model.supports_vision) == ("plain", False)

    def test_an_unfamiliar_modality_spelling_reads_as_saying_nothing(self) -> None:
        (model,) = _parse_openai_compatible_models(
            {"data": [{"id": "odd", "architecture": {"modality": "multimodal"}}]}
        )

        assert (model.name, model.supports_vision) == ("odd", False)
