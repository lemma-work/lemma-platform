from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest

from app.modules.workspace.services.port_access import (
    PortAccessInvalid,
    PortAccessSigner,
    PortGrant,
)

KEY = b"k" * 32


def _grant(**overrides) -> PortGrant:
    defaults = {
        "sandbox_id": uuid4(),
        "port": 4848,
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=10),
    }
    defaults.update(overrides)
    return PortGrant(**defaults)  # type: ignore[arg-type]


def test_a_signed_grant_round_trips() -> None:
    signer = PortAccessSigner(key=KEY)
    grant = _grant()
    verified = signer.verify(signer.sign(grant))

    assert verified.sandbox_id == grant.sandbox_id
    assert verified.port == grant.port
    assert int(verified.expires_at.timestamp()) == int(grant.expires_at.timestamp())


def test_the_signature_covers_the_sandbox_so_a_port_cannot_be_repointed() -> None:
    """Otherwise a grant for your own workspace would open anyone else's."""
    signer = PortAccessSigner(key=KEY)
    mine = signer.sign(_grant(sandbox_id=uuid4()))
    _, _, signature = mine.partition(".")

    forged_payload = (
        PortAccessSigner(key=KEY).sign(_grant(sandbox_id=uuid4())).split(".")[0]
    )
    with pytest.raises(PortAccessInvalid):
        signer.verify(f"{forged_payload}.{signature}")


def test_the_signature_covers_the_port() -> None:
    signer = PortAccessSigner(key=KEY)
    sandbox_id = uuid4()
    expires = datetime.now(timezone.utc) + timedelta(minutes=10)
    browser = signer.sign(_grant(sandbox_id=sandbox_id, port=4848, expires_at=expires))
    runtime_payload = signer.sign(
        _grant(sandbox_id=sandbox_id, port=8080, expires_at=expires)
    ).split(".")[0]

    with pytest.raises(PortAccessInvalid):
        signer.verify(f"{runtime_payload}.{browser.split('.')[1]}")


def test_an_expired_grant_is_refused() -> None:
    """Revocation is the clock, so the expiry has to be enforced on read."""
    signer = PortAccessSigner(key=KEY)
    token = signer.sign(
        _grant(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    )
    with pytest.raises(PortAccessInvalid, match="expired"):
        signer.verify(token)


def test_a_grant_signed_with_another_key_is_refused() -> None:
    token = PortAccessSigner(key=b"a" * 32).sign(_grant())
    with pytest.raises(PortAccessInvalid):
        PortAccessSigner(key=b"b" * 32).verify(token)


@pytest.mark.parametrize("token", ["", "nodot", ".", "abc.", ".abc", "a.b.c"])
def test_malformed_tokens_are_refused_rather_than_crashing(token: str) -> None:
    with pytest.raises(PortAccessInvalid):
        PortAccessSigner(key=KEY).verify(token)


def test_a_re_encoded_payload_does_not_verify() -> None:
    """The MAC covers the exact bytes presented, so an equivalent-but-different
    base64 encoding of the same claims must not be accepted."""
    signer = PortAccessSigner(key=KEY)
    payload, _, signature = signer.sign(_grant()).partition(".")
    with pytest.raises(PortAccessInvalid):
        signer.verify(f"{payload}=.{signature}")


def test_a_short_key_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        PortAccessSigner(key=b"tooshort")


def test_a_proxied_page_says_who_may_frame_it() -> None:
    """A signed URL is a bearer token in a link, and links leak. `frame-ancestors`
    is what stops a leaked one being framed by somebody else's page and driven
    from there."""
    from app.modules.workspace.api.controllers.port_proxy_controller import (
        _STRIPPED_RESPONSE_HEADERS,
        _frame_ancestors,
    )

    ancestors = _frame_ancestors()
    assert ancestors
    assert "*" not in ancestors
    # The sandbox's own opinion about framing is never forwarded — the answer
    # belongs to the proxy.
    assert "content-security-policy" in _STRIPPED_RESPONSE_HEADERS
    assert "x-frame-options" in _STRIPPED_RESPONSE_HEADERS


def test_the_grants_own_url_shape_reaches_the_proxy() -> None:
    """The minted URL ends at the token with a trailing slash and no path.

    `/{token}/{path:path}` alone does not match that, so the one URL this proxy
    exists to hand out 404'd while every deeper path worked — which is exactly
    the shape nothing tested.
    """
    from app.modules.workspace.api.controllers.port_proxy_controller import router

    paths = {getattr(route, "path", "") for route in router.routes}
    assert "/workspace-ports/{token}" in paths
    assert "/workspace-ports/{token}/{path:path}" in paths


def test_the_proxy_is_not_behind_the_session_gate() -> None:
    """The signed grant in the path IS the credential, and this URL is handed to
    a browser that has no Lemma session and never will."""
    from app.core.security import EXCLUDED_PATHS

    assert any(path.startswith("/workspace-ports") for path in EXCLUDED_PATHS)


def test_the_proxy_carries_whatever_the_fabrics_own_door_needs() -> None:
    """The request half has to send the headers `reach_port` hands back.

    On E2B a closed sandbox answers 403 without its per-sandbox traffic token,
    and `reach_port` returns it as `SandboxEndpoint.headers`. The WebSocket half
    has always forwarded those; this half dropped them, and nothing noticed
    because no sandbox had ever actually been created closed -- the flag meant
    to close them raised `TypeError` and was never in effect.

    Dropping ours was not the whole of it. The caller's headers were forwarded
    verbatim, so a holder of a signed link could put their own
    `e2b-traffic-access-token` on the request and have it passed to the sandbox
    as the only one. The fabric's go last for that reason: the sandbox's
    doorkeeper is not something the person holding the link gets to choose.
    """
    from app.modules.workspace.api.controllers.port_proxy_controller import (
        _upstream_headers,
    )

    sent = _upstream_headers(
        {
            "e2b-traffic-access-token": "forged-by-the-caller",
            "cookie": "lemma_session=hunter2",
            "x-harmless": "kept",
        },
        {"e2b-traffic-access-token": "the-real-one"},
    )

    assert sent["e2b-traffic-access-token"] == "the-real-one"
    assert sent["x-harmless"] == "kept"
    assert "cookie" not in sent


class _TrackedStream(httpx.AsyncByteStream):
    """An upstream body that records how far it was read and whether closed."""

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.yielded = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.yielded += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def _client_answering(stream: _TrackedStream, **headers: str) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers=headers, stream=stream)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_the_upstream_body_is_streamed_not_buffered() -> None:
    from starlette.responses import StreamingResponse

    from app.modules.workspace.api.controllers.port_proxy_controller import _forward

    stream = _TrackedStream([b"a", b"b", b"c"])
    async with _client_answering(stream, **{"content-encoding": "gzip"}) as client:
        response = await _forward(client, httpx.Request("GET", "http://sandbox/"))

        assert isinstance(response, StreamingResponse)
        # Nothing read before the ASGI server starts pulling.
        assert stream.yielded == 0
        # Raw bytes under the upstream's own encoding.
        assert response.headers["content-encoding"] == "gzip"
        body = [chunk async for chunk in response.body_iterator]
        assert body == [b"a", b"b", b"c"]
        assert stream.closed


async def test_the_upstream_is_closed_when_the_caller_leaves_before_the_body() -> None:
    """A disconnect before streaming never iterates the body; the background
    task Starlette always runs is what closes the upstream then."""
    from app.modules.workspace.api.controllers.port_proxy_controller import _forward

    stream = _TrackedStream([b"never read"])
    async with _client_answering(stream) as client:
        response = await _forward(client, httpx.Request("GET", "http://sandbox/"))
        assert response.background is not None
        await response.background()
        assert stream.closed
        assert stream.yielded == 0


async def test_an_unreachable_upstream_is_a_502() -> None:
    from app.modules.workspace.api.controllers.port_proxy_controller import _forward

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await _forward(client, httpx.Request("GET", "http://sandbox/"))
    assert response.status_code == 502


async def test_the_proxy_reuses_one_client_and_closes_it_on_shutdown() -> None:
    from app.modules.workspace.api.controllers import port_proxy_controller as proxy

    first = proxy.get_port_proxy_client()
    assert proxy.get_port_proxy_client() is first
    await proxy.close_port_proxy_client()
    assert first.is_closed
    assert proxy.get_port_proxy_client() is not first
    await proxy.close_port_proxy_client()


async def test_the_shared_client_keeps_no_sandbox_cookies() -> None:
    """The client is process-wide; a sandbox issuing fresh cookies must not
    grow it. The jar refuses everything a response tries to set."""
    from app.modules.workspace.api.controllers import port_proxy_controller as proxy

    client = proxy.get_port_proxy_client()
    try:
        client.cookies.extract_cookies(
            httpx.Response(
                200,
                headers={"set-cookie": "session=abc; Path=/"},
                request=httpx.Request("GET", "http://sandbox/"),
            )
        )
        assert len(client.cookies.jar) == 0
    finally:
        await proxy.close_port_proxy_client()


@pytest.mark.parametrize(
    ("method", "headers", "expected"),
    [
        ("GET", [(b"content-length", b"5")], True),
        ("OPTIONS", [(b"transfer-encoding", b"chunked")], True),
        ("POST", [(b"content-length", b"0")], False),
        ("GET", [], False),
    ],
)
def test_a_body_is_forwarded_when_one_was_sent_whatever_the_method(
    method: str, headers: list[tuple[bytes, bytes]], expected: bool
) -> None:
    from starlette.requests import Request

    from app.modules.workspace.api.controllers.port_proxy_controller import _has_body

    request = Request({"type": "http", "method": method, "headers": headers})
    assert _has_body(request) is expected
