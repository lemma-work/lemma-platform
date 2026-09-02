"""What a webhook source is, and which ones exist.

`POST /webhooks/{source}` takes the source from the URL, so `source` is chosen
by whoever sends the request. Everything after it -- schedule matching, the run
that starts, the first message an agent sees -- runs on a body nobody
authenticated unless this layer says otherwise. The registry below *is* that
allow-list: a source with no plugin is refused, and adding one is adding a
plugin rather than adding a branch that someone can forget to guard.

That is the whole reason this is a registry and not an `if`. The previous shape
verified Composio in one branch and refused everything else in the other, with a
comment explaining that the refusal was the security property. It was right, and
it does not survive a third source being added by someone reading only the
happy path.

Two steps, not three. Verification and normalization are separate because they
fail differently -- a bad signature is an attack or a misconfiguration, an
unrecognised event is neither -- but working out *which tenant* a delivery is
for stays inside them. For a source whose secret is global (one GitHub App per
environment, one Composio webhook secret) the tenant is not needed to verify,
and for one whose secret is per-account it has to be found during verification
rather than after it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

#: A provider's webhook body, parsed. Deliberately untyped: it is third-party
#: JSON whose shape is the provider's to change without telling us, and the
#: plugins are what turn it into something with a shape.
WebhookPayload = dict[str, Any]


@dataclass(frozen=True, slots=True)
class WebhookDelivery:
    """One inbound request, before anything has been believed about it.

    `raw_body` is the bytes as they arrived, and every signature scheme needs
    exactly those: re-serializing parsed JSON changes whitespace and key order
    and produces a different digest, which presents as an authentication
    failure with no explanation.
    """

    source: str
    raw_body: bytes
    headers: Mapping[str, str]

    def header(self, name: str) -> str | None:
        """A header by name, case-insensitively.

        HTTP header names are case-insensitive and providers do not agree with
        each other about casing -- GitHub sends `X-GitHub-Event`, Composio sends
        `webhook-id`. Starlette lowercases on the way in, but this takes a plain
        mapping so tests and any future non-HTTP transport can build one.
        """
        lowered = name.lower()
        for key, value in self.headers.items():
            if key.lower() == lowered:
                return value
        return None


@dataclass(frozen=True, slots=True)
class VerifiedDelivery:
    """A delivery that proved where it came from, and its parsed body."""

    delivery: WebhookDelivery
    payload: WebhookPayload


@dataclass(frozen=True, slots=True)
class NormalizedWebhook:
    """What a verified delivery becomes: a routing key and a payload.

    `source_event_id` is what makes a redelivery idempotent, so it must be
    derived from the *event* rather than from the delivery. Providers redeliver
    with a new delivery id -- that is what a redelivery is -- so using theirs
    would run the schedule a second time for the same push.
    """

    payload: WebhookPayload
    source_event_id: str | None = None
    #: The routing key this delivery is matched on, checked against a schedule's
    #: stored config by JSONB containment. `None` leaves the legacy
    #: `WebhookEventMapper` to derive it, which is what the sources that predate
    #: the plugins still do.
    match: WebhookPayload | None = None
    #: A second pass over the schedules the routing key returned, given each
    #: one's config. Containment runs `config @> criteria`, so every key in the
    #: routing key must be present in *every* schedule that could match -- which
    #: makes an optional narrowing key (only this repository, only these
    #: actions) impossible to express in it. This is where those live, so the
    #: knowledge of what they mean stays with the source that defined them.
    refine: Callable[[WebhookPayload], bool] | None = None
    #: Extra metadata that rides along with the run but is *not* matched on --
    #: it cannot be, since matching is containment against the schedule's stored
    #: config and a schedule declares none of this. It is how a source says
    #: something about the delivery that the run needs to know: for GitHub, the
    #: repository the event happened in, so the agent wakes up standing in it.
    context: WebhookPayload = field(default_factory=dict)


class WebhookNotVerified(Exception):
    """This delivery did not prove it came from the source it claims.

    Carries no detail outward on purpose: everything that would be useful in it
    is also useful to whoever is guessing at the signature.
    """


@runtime_checkable
class WebhookSourcePlugin(Protocol):
    """One provider's half of the inbound path."""

    @property
    def source(self) -> str:
        """The `{source}` segment this plugin answers for."""
        ...

    async def verify(self, delivery: WebhookDelivery) -> VerifiedDelivery:
        """Prove the delivery came from this source, and parse it.

        Raises `WebhookNotVerified` if it did not. Async because verification
        may run a provider SDK or reach for a stored secret, and this sits on a
        path whose rate an external sender chooses -- a synchronous
        implementation blocks the event loop once per delivery.
        """
        ...

    async def observe(self, verified: VerifiedDelivery) -> None:
        """React to a delivery that changes the *source's own* state.

        Separate from `normalize` because it is not about matching a schedule:
        an App being uninstalled starts nothing, it invalidates things. Without
        it an uninstall is silent -- accounts go on claiming to be connected,
        their schedules go on existing, and every delivery simply stops. Silence
        is the failure mode this whole path keeps producing, so it gets a step
        of its own rather than a branch inside one.

        Runs before `normalize` and must not raise: whatever it is reacting to
        has already happened, and refusing the delivery would only make the
        provider redeliver it.
        """
        ...

    def normalize(self, verified: VerifiedDelivery) -> NormalizedWebhook | None:
        """The routing key and payload, or None to acknowledge and do nothing.

        None is the common case for a busy source and is not a failure: a
        delivery for an event nothing is subscribed to must be answered 2xx, or
        the provider retries it and then disables the hook.
        """
        ...


class WebhookSourceRegistry:
    """The sources this deployment accepts. Absence is a refusal."""

    def __init__(self, plugins: Iterable[WebhookSourcePlugin]) -> None:
        self._plugins: dict[str, WebhookSourcePlugin] = {}
        for plugin in plugins:
            key = plugin.source.strip().lower()
            if key in self._plugins:
                raise ValueError(f"Two plugins claim the webhook source '{key}'.")
            self._plugins[key] = plugin

    def for_source(self, source: str) -> WebhookSourcePlugin | None:
        return self._plugins.get((source or "").strip().lower())

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(sorted(self._plugins))
