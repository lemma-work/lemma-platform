"""JSON-schema compatibility for OpenAI-compatible model providers.

Some OpenAI-compatible providers (notably Fireworks' GLM models) cannot resolve
``$ref`` -> ``#/$defs/...`` in tool/output JSON schemas server-side. They reject
the request with e.g.::

    Error resolving schema reference '#/$defs/DisplayResourceType':
    AttributeError("'NoneType' object has no attribute 'lookup'")

pydantic-ai's default OpenAI transformer keeps ``$defs``/``$ref`` in place and
relies on the provider to resolve them (OpenAI itself does). Any tool whose
arguments include a Pydantic model or enum (e.g. ``display_resource``'s
``DisplayResourceType``) therefore breaks on these providers.

We swap in a transformer that first inlines every non-recursive ``$ref`` — so no
provider-side reference resolution is ever needed — then applies the normal
OpenAI strict-mode normalisation. Inlining is provider-agnostic and safe:
fully-inlined schemas are equivalent and accepted by OpenAI too. Recursive
schemas (which cannot be inlined) keep a minimal ``$defs``/``$ref`` structure,
exactly as before.
"""

from __future__ import annotations

from pydantic_ai.profiles import ModelProfile, merge_profile
from pydantic_ai.profiles.openai import OpenAIJsonSchemaTransformer

from app.modules.agent.infrastructure.pydantic_ai_compat import (
    InlineDefsJsonSchemaTransformer,
    JsonSchema,
    JsonSchemaTransformer,
)


class InlineDefsOpenAIJsonSchemaTransformer(JsonSchemaTransformer):
    """OpenAI strict-mode transformer that first inlines ``$defs``/``$ref``."""

    def transform(self, schema: JsonSchema) -> JsonSchema:
        # Unused: walk() composes two concrete transformers instead.
        return schema

    def walk(self) -> JsonSchema:
        inlined = InlineDefsJsonSchemaTransformer(
            self.schema, strict=self.strict
        ).walk()
        openai = OpenAIJsonSchemaTransformer(inlined, strict=self.strict)
        result = openai.walk()
        # Propagate strict-compatibility so pydantic-ai infers each tool/output
        # `strict` flag from the actual (post-inline) schema, not our default.
        self.is_strict_compatible = openai.is_strict_compatible
        return result


def openai_compatible_model_profile(resolved: ModelProfile) -> ModelProfile:
    """The provider's own profile, but with ``$defs`` inlined in tool schemas.

    pydantic-ai hands a ``profile=`` callable the profile it already resolved
    from the provider and model name, so every trait it picked is preserved and
    only the JSON-schema transformer is overridden. (In pydantic-ai 1.x the
    callable received the model *name* and had to re-derive the base profile
    itself; 2.x resolves it first, and the callable's return value bypasses
    ``merge_profile``, so merging here is what keeps the rest of the profile.)
    """
    return merge_profile(
        resolved,
        ModelProfile(json_schema_transformer=InlineDefsOpenAIJsonSchemaTransformer),
    )
