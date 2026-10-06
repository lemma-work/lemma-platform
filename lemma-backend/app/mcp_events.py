"""`events/*` on the pod MCP endpoint: the webhook slice of MCP Events.

ChatGPT subscribes to a server's events by reading `capabilities.events` from
`server/discover` and calling `events/list`, `events/subscribe` and
`events/unsubscribe`. None of these are in the MCP SDK -- the extension is a
working-group draft -- so they are bound here as a FastMCP server extension,
gated to protocol 2026-07-28 as ChatGPT's integration is, and everything they
decide is `mcp_access`'s.

`capabilities.events` itself cannot be set the ordinary way: the SDK sieves a
spec result against the spec's own model, which has no `events` key. The
discover middleware below adds it to the result after the sieve, and the
extension is advertised under `capabilities.extensions` as well.

Only outside clients subscribe. The Agent Host's mount reaches the same server
with no principal, and is told so.
"""

from __future__ import annotations

from collections.abc import Callable
from fastmcp.server.context import ServerRequestContext
from fastmcp.server.extensions import MethodBinding, ServerExtension
from fastmcp.server.middleware.middleware import CallNext, Middleware, MiddlewareContext
from mcp.shared.exceptions import MCPError
import mcp_types as mt
from mcp_types import RequestParams
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.modules.mcp_access.contracts import McpPrincipal
from app.modules.mcp_access.contracts.events import (
    EventSubscriptionError,
    SubscribeRequest,
    list_events,
    subscribe_to_event,
    unsubscribe_from_event,
)

EVENTS_PROTOCOL = frozenset({"2026-07-28"})


class _Delivery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    mode: str = "webhook"
    url: str
    secret: str | None = None


class ListEventsParams(RequestParams):
    cursor: str | None = None


class SubscribeParams(RequestParams):
    name: str
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    delivery: _Delivery
    cursor: str | None = None
    max_age_ms: int | None = Field(default=None, alias="maxAgeMs")
    ttl_ms: int | None = Field(default=None, alias="ttlMs")


class UnsubscribeParams(RequestParams):
    name: str
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    delivery: _Delivery


def _as_mcp_error(exc: EventSubscriptionError) -> MCPError:
    return MCPError(code=exc.code, message=exc.message, data=exc.data)


class PodEventsExtension(ServerExtension):
    identifier = "work.lemma/events"

    def __init__(self, principal: Callable[[], McpPrincipal | None]) -> None:
        self._principal = principal

    def methods(self) -> list[MethodBinding]:
        return [
            MethodBinding(
                "events/list",
                ListEventsParams,
                self._serve_events_list,
                EVENTS_PROTOCOL,
            ),
            MethodBinding(
                "events/subscribe",
                SubscribeParams,
                self._serve_events_subscribe,
                EVENTS_PROTOCOL,
            ),
            MethodBinding(
                "events/unsubscribe",
                UnsubscribeParams,
                self._serve_events_unsubscribe,
                EVENTS_PROTOCOL,
            ),
        ]

    async def _serve_events_list(
        self, ctx: ServerRequestContext[object, object], params: ListEventsParams
    ) -> dict[str, JsonValue]:
        del ctx, params
        try:
            return list_events(self._principal())
        except EventSubscriptionError as exc:
            raise _as_mcp_error(exc) from exc

    async def _serve_events_subscribe(
        self, ctx: ServerRequestContext[object, object], params: SubscribeParams
    ) -> dict[str, JsonValue]:
        del ctx
        try:
            return await subscribe_to_event(
                self._principal(),
                SubscribeRequest(
                    name=params.name,
                    arguments=params.arguments,
                    mode=params.delivery.mode,
                    url=params.delivery.url,
                    secret=params.delivery.secret or "",
                    ttl_ms=params.ttl_ms,
                ),
            )
        except EventSubscriptionError as exc:
            raise _as_mcp_error(exc) from exc

    async def _serve_events_unsubscribe(
        self, ctx: ServerRequestContext[object, object], params: UnsubscribeParams
    ) -> dict[str, JsonValue]:
        del ctx
        try:
            return await unsubscribe_from_event(
                self._principal(),
                name=params.name,
                arguments=params.arguments,
                url=params.delivery.url,
            )
        except EventSubscriptionError as exc:
            raise _as_mcp_error(exc) from exc


class AdvertiseEvents(Middleware):
    """Put `capabilities.events` on `server/discover`, after the SDK's sieve."""

    async def on_discover(
        self,
        context: MiddlewareContext[mt.DiscoverRequest],
        call_next: CallNext[mt.DiscoverRequest, mt.DiscoverResult | dict[str, object]],
    ) -> mt.DiscoverResult | dict[str, object]:
        result = await call_next(context)
        found = (
            result.model_dump(mode="json", by_alias=True, exclude_none=True)
            if isinstance(result, BaseModel)
            else dict(result)
        )
        capabilities = dict(found.get("capabilities") or {})
        capabilities.setdefault("events", {})
        found["capabilities"] = capabilities
        return found
