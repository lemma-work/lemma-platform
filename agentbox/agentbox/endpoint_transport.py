from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import Callable
from urllib import error as urlerror
from urllib import request as urlrequest

from agentbox.providers.models import TransientGateway


logger = logging.getLogger(__name__)

REQUEST_NOT_DELIVERED_HEADER = "X-Agentbox-Request-Not-Delivered"

# Real E2B resume tests observed authenticated routes disappear for longer than
# three seconds after successful health and session requests. Replaying these
# exact provider-generated pre-routing failures is safe; generic 502s still
# return immediately.
_TRANSIENT_GATEWAY_RETRY_SECONDS = 15.0
_TRANSIENT_GATEWAY_INITIAL_RETRY_SECONDS = 0.25
_TRANSIENT_GATEWAY_MAX_RETRY_SECONDS = 2.0
_TRANSIENT_GATEWAY_LOG_ATTEMPTS = frozenset({1, 4, 8})
_IDEMPOTENT_HTTP_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


class EndpointRoutingUnavailable(RuntimeError):
    """A provider proved that a request never reached the sandbox route.

    This signal is deliberately narrower than an HTTP 502.  Callers may use it
    to refresh provider routing and replay a request because
    :func:`is_transient_gateway_response` has matched the provider-native
    sandbox identity (and port, when the response names one).
    """

    def __init__(
        self,
        *,
        gateway: TransientGateway,
        instance_id: str,
        port: int | None,
    ) -> None:
        super().__init__(
            f"{gateway} endpoint routing remained unavailable for sandbox {instance_id}"
        )
        self.gateway = gateway
        self.instance_id = instance_id
        self.port = port


def is_transient_gateway_response(
    gateway: TransientGateway | None,
    status_code: int,
    content: bytes,
    *,
    expected_instance_id: str | None,
    expected_port: int | None,
) -> bool:
    """Recognize a provider response which proves the request was not routed.

    This intentionally matches only E2B's structured gateway response. A
    generic 502 is ambiguous and must never cause a non-idempotent request to
    be replayed.
    """

    if gateway != "e2b" or status_code != 502:
        return False
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if (
        not isinstance(payload, dict)
        or not expected_instance_id
        or payload.get("sandboxId") != expected_instance_id
        or payload.get("code") != 502
    ):
        return False
    if payload.get("message") == "The sandbox was not found":
        return True
    return (
        payload.get("message") == "The sandbox is running but port is not open"
        and expected_port is not None
        and payload.get("port") == expected_port
    )


def request_endpoint_http(
    req: urlrequest.Request,
    *,
    timeout: int | float,
    transient_gateway: TransientGateway | None = None,
    expected_instance_id: str | None = None,
    expected_port: int | None = None,
    retry_seconds: float = _TRANSIENT_GATEWAY_RETRY_SECONDS,
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
    jitter: Callable[[float, float], float] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    """Make one HTTP request, retrying only a proven provider routing miss."""

    clock = clock or time.monotonic
    sleep = sleep or time.sleep
    jitter = jitter or random.uniform
    deadline = clock() + max(retry_seconds, 0.0)
    attempt = 0
    retry_delay = _TRANSIENT_GATEWAY_INITIAL_RETRY_SECONDS
    while True:
        attempt += 1
        try:
            with urlrequest.urlopen(req, timeout=timeout) as response:
                result = (
                    int(getattr(response, "status", 200)),
                    dict(getattr(response, "headers", {}).items()),
                    response.read(),
                )
        except urlerror.HTTPError as exc:
            try:
                result = (
                    exc.code,
                    dict(exc.headers.items()) if exc.headers is not None else {},
                    exc.read(),
                )
            finally:
                exc.close()

        status_code, _, content = result
        remaining = deadline - clock()
        transient_routing_miss = is_transient_gateway_response(
            transient_gateway,
            status_code,
            content,
            expected_instance_id=expected_instance_id,
            expected_port=expected_port,
        )
        # The E2B gateway body isn't cryptographically authenticated. A
        # sandbox-hosted application can forge it, so only intrinsically
        # idempotent HTTP methods may be retried. POST/PUT/PATCH/DELETE are
        # always surfaced to the caller, even when they carry a run ID.
        replay_safe = req.get_method().upper() in _IDEMPOTENT_HTTP_METHODS
        transient_routing_miss = transient_routing_miss and replay_safe
        if not transient_routing_miss:
            return result
        if remaining <= 0:
            # Do not return this as an ordinary upstream 502.  The async
            # provider boundary needs a distinct signal so it can discard a
            # stale endpoint/token, reconnect once, and safely replay only
            # this proven pre-routing miss.
            assert transient_gateway is not None
            assert expected_instance_id is not None
            raise EndpointRoutingUnavailable(
                gateway=transient_gateway,
                instance_id=expected_instance_id,
                port=expected_port,
            )

        base_delay = min(retry_delay, remaining)
        delay = min(
            base_delay + jitter(0.0, min(base_delay * 0.2, 0.1)),
            remaining,
        )
        # A cold E2B route may need several retries. Log a bounded sample so a
        # burst of concurrent sandbox starts cannot create a synchronized log
        # storm while still leaving enough breadcrumbs for diagnosis.
        if attempt in _TRANSIENT_GATEWAY_LOG_ATTEMPTS or delay >= remaining:
            logger.warning(
                "Sandbox endpoint gateway was temporarily unroutable; "
                "gateway=%s attempt=%d retry_in_seconds=%.3f",
                transient_gateway,
                attempt,
                delay,
            )
        sleep(delay)
        retry_delay = min(
            retry_delay * 1.5,
            _TRANSIENT_GATEWAY_MAX_RETRY_SECONDS,
        )
