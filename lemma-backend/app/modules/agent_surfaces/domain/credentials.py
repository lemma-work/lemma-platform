"""What an adapter authenticates with, one model per platform.

Credentials used to travel as one mapping from the resolver through routing,
the turn starter and every adapter call, each reader pulling the keys it knew
about with `.get` -- which is how a field nobody joined in stayed missing for
three releases, and how a revealed bot token came to sit in a long-lived
mapping that any `repr` or log line could print.

Each platform says here which fields it has, the secrets among them are
`SecretStr`, and the plaintext is read with `reveal_secret` at the HTTP call
that needs it. A stored account payload is free-form -- a provider's OAuth
response nests what matters a level down -- so `from_payload` is where the
spellings are reconciled, once, and nothing past it has to know them.

`of` accepts a mapping as well as a model. That is the boundary where a raw
payload still arrives -- an account row read for a native receiver, a test
building credentials by hand -- and it is validated there rather than passed on
as a mapping.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar, Self

from pydantic import BaseModel, ConfigDict, SecretStr

from app.modules.agent_surfaces.domain.entities import SurfacePlatform

# A stored payload: provider JSON with no shape until a model reads it.
CredentialPayload = Mapping[str, object]


def _nested(payload: CredentialPayload, key: str) -> CredentialPayload:
    value = payload.get(key)
    return value if isinstance(value, Mapping) else {}


def _text(*candidates: object) -> str | None:
    """The first candidate that is a non-empty string, stripped."""
    for candidate in candidates:
        if candidate is None:
            continue
        text = str(candidate).strip()
        if text:
            return text
    return None


def as_secret(*candidates: object) -> SecretStr | None:
    """The first candidate with a value, as a secret. A `SecretStr` passes
    through unrevealed; a plain string (a test setting, a stored payload) is
    wrapped."""
    for candidate in candidates:
        if isinstance(candidate, SecretStr):
            if candidate.get_secret_value():
                return candidate
            continue
        text = _text(candidate)
        if text:
            return SecretStr(text)
    return None


def _scopes(*candidates: object) -> frozenset[str]:
    found: set[str] = set()
    for candidate in candidates:
        if isinstance(candidate, str):
            found.update(part.strip() for part in candidate.split(","))
        elif isinstance(candidate, list | tuple | set | frozenset):
            found.update(str(item).strip() for item in candidate)
    return frozenset(scope for scope in found if scope)


class SurfaceCredentials(BaseModel):
    """The fields every platform's credentials share."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    platform: ClassVar[SurfacePlatform]

    #: Where the platform's API lives, when it is not the public one: a test
    #: server, a self-hosted gateway. Tenant-supplied, so callers vet it.
    api_base_url: str | None = None
    #: Which kind of auth config the account was connected through, if any.
    provider: str | None = None

    @classmethod
    def from_payload(cls, payload: CredentialPayload) -> Self:
        """Read a stored or settings-built payload. Unknown keys are ignored."""
        raise NotImplementedError

    @classmethod
    def of(cls, value: "SurfaceCredentials | CredentialPayload | None") -> Self:
        """This platform's credentials, from a model or from a raw payload.

        A model of another platform is refused: it means a surface's
        credentials were handed to the wrong adapter, and reading them as
        empty would turn that into an unexplained authentication failure.
        """
        if isinstance(value, cls):
            return value
        if isinstance(value, SurfaceCredentials):
            raise TypeError(
                f"{type(value).__name__} handed to a {cls.platform.value} adapter"
            )
        return cls.from_payload(value or {})


class SlackCredentials(SurfaceCredentials):
    platform: ClassVar[SurfacePlatform] = SurfacePlatform.SLACK

    #: The token every Web API call carries: the user/bot OAuth token.
    token: SecretStr | None = None
    #: Socket Mode's app-level token; set only where Socket Mode is used.
    app_token: SecretStr | None = None
    scopes: frozenset[str] = frozenset()

    @classmethod
    def from_payload(cls, payload: CredentialPayload) -> Self:
        raw = _nested(payload, "raw_response")
        return cls(
            token=as_secret(
                payload.get("access_token"),
                payload.get("bot_token"),
                raw.get("access_token"),
            ),
            app_token=as_secret(payload.get("app_token"), raw.get("app_token")),
            scopes=_scopes(
                payload.get("scope"),
                payload.get("scopes"),
                raw.get("scope"),
                raw.get("scopes"),
            ),
            api_base_url=_text(payload.get("api_base_url"), raw.get("api_base_url")),
            provider=_text(payload.get("provider")),
        )


class TeamsCredentials(SurfaceCredentials):
    """Teams authenticates as the deployment's bot, from settings; an account
    contributes only which tenant it is, and where the APIs are."""

    platform: ClassVar[SurfacePlatform] = SurfacePlatform.TEAMS

    tenant_id: str | None = None
    graph_api_base_url: str | None = None
    bot_service_base_url: str | None = None

    @classmethod
    def from_payload(cls, payload: CredentialPayload) -> Self:
        user_data = _nested(payload, "user_data")
        raw = _nested(payload, "raw_response")
        return cls(
            tenant_id=_text(
                user_data.get("tenant_id"),
                user_data.get("tid"),
                raw.get("tenant_id"),
                raw.get("tid"),
                payload.get("tenant_id"),
                payload.get("tid"),
            ),
            graph_api_base_url=_text(payload.get("graph_api_base_url")),
            bot_service_base_url=_text(payload.get("bot_service_base_url")),
            api_base_url=_text(payload.get("api_base_url")),
            provider=_text(payload.get("provider")),
        )


class TelegramCredentials(SurfaceCredentials):
    platform: ClassVar[SurfacePlatform] = SurfacePlatform.TELEGRAM

    bot_token: SecretStr | None = None

    @classmethod
    def from_payload(cls, payload: CredentialPayload) -> Self:
        return cls(
            bot_token=as_secret(payload.get("bot_token")),
            api_base_url=_text(payload.get("api_base_url")),
            provider=_text(payload.get("provider")),
        )


class WhatsAppCredentials(SurfaceCredentials):
    platform: ClassVar[SurfacePlatform] = SurfacePlatform.WHATSAPP

    access_token: SecretStr | None = None
    phone_number_id: str | None = None
    waba_id: str | None = None
    #: What Meta signs webhooks with, for the account's own app.
    app_secret: SecretStr | None = None
    #: What Meta echoes when it verifies the webhook subscription.
    verify_token: SecretStr | None = None
    #: The number as a person dials it, where the account recorded it.
    display_phone_number: str | None = None

    @classmethod
    def from_payload(cls, payload: CredentialPayload) -> Self:
        return cls(
            access_token=as_secret(payload.get("access_token")),
            phone_number_id=_text(payload.get("phone_number_id")),
            waba_id=_text(payload.get("waba_id")),
            app_secret=as_secret(payload.get("app_secret")),
            verify_token=as_secret(payload.get("verify_token")),
            display_phone_number=_text(
                payload.get("display_phone_number"), payload.get("phone_number")
            ),
            api_base_url=_text(payload.get("api_base_url")),
            provider=_text(payload.get("provider")),
        )

    def overlaid_with(self, overrides: Mapping[str, str]) -> WhatsAppCredentials:
        """These credentials, with what a pooled number says laid over them.

        Only the fields the number has: a field it leaves out keeps the
        deployment's answer, which is how a pool of numbers sharing one app
        needs no duplicated secrets.
        """
        update: dict[str, str | SecretStr] = {}
        for field in ("phone_number_id", "waba_id"):
            text = _text(overrides.get(field))
            if text:
                update[field] = text
        for field in ("access_token", "app_secret"):
            secret = as_secret(overrides.get(field))
            if secret is not None:
                update[field] = secret
        return self.model_copy(update=update) if update else self


class ResendCredentials(SurfaceCredentials):
    platform: ClassVar[SurfacePlatform] = SurfacePlatform.RESEND

    api_key: SecretStr | None = None
    #: The surface's own address. A property of the surface, not the account:
    #: joined in by the resolver from the surface row.
    from_address: str | None = None
    from_name: str | None = None

    @classmethod
    def from_payload(cls, payload: CredentialPayload) -> Self:
        return cls(
            api_key=as_secret(payload.get("api_key")),
            from_address=_text(payload.get("from_address")),
            from_name=_text(payload.get("from_name")),
            api_base_url=_text(payload.get("api_base_url")),
            provider=_text(payload.get("provider")),
        )

    def sending_as(self, address: str) -> ResendCredentials:
        return self.model_copy(update={"from_address": address})


#: What a platform's own helpers accept: a model, read through ``of``, or a
#: raw payload still on its way to becoming one.
CredentialsLike = SurfaceCredentials | CredentialPayload


_BY_PLATFORM: dict[SurfacePlatform, type[SurfaceCredentials]] = {
    model.platform: model
    for model in (
        SlackCredentials,
        TeamsCredentials,
        TelegramCredentials,
        WhatsAppCredentials,
        ResendCredentials,
    )
}


def credentials_model_for(platform: SurfacePlatform) -> type[SurfaceCredentials]:
    return _BY_PLATFORM[platform]


def credentials_for(
    platform: SurfacePlatform | str, payload: CredentialPayload | None
) -> SurfaceCredentials:
    """The platform's credentials, read from a raw payload."""
    resolved = SurfacePlatform(str(platform).upper())
    return _BY_PLATFORM[resolved].from_payload(payload or {})


__all__ = [
    "CredentialPayload",
    "CredentialsLike",
    "ResendCredentials",
    "SlackCredentials",
    "SurfaceCredentials",
    "TeamsCredentials",
    "TelegramCredentials",
    "WhatsAppCredentials",
    "as_secret",
    "credentials_for",
    "credentials_model_for",
]
