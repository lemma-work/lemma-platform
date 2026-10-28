"""The API's decisions about a WebRTC browser view.

Who is in the rollout, what a ticket says and how it is signed, which TURN
servers each end is given, and -- the part the security model rests on --
which browser messages reach the streamer at all.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from uuid import UUID, uuid4

import pytest

from app.modules.workspace.config import workspace_settings
from app.modules.workspace.services import browser_stream_turn
from app.modules.workspace.services.browser_stream import (
    TICKET_TTL_SECONDS,
    display_decision,
    mint_ticket,
    stream_key,
    stream_key_file,
    transport_for,
)
from app.modules.workspace.services.browser_stream_turn import (
    Coturn,
    _cloudflare_servers,
    with_tls_443,
)
from app.modules.workspace.services.browser_view_rtc import (
    RtcViewer,
    config_message,
    from_browser,
    hello_message,
)


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setattr(workspace_settings, "runtime_credential_key", "k" * 32)


def _decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def _verify(ticket: str, key_hex: str) -> dict[str, object]:
    """The streamer's check, written out independently of the minting code."""
    payload, _, signature = ticket.partition(".")
    expected = hmac.new(bytes.fromhex(key_hex), payload.encode(), hashlib.sha256)
    assert hmac.compare_digest(expected.digest(), _decode(signature))
    return json.loads(_decode(payload))


# --- tickets ---------------------------------------------------------------


def test_a_ticket_names_the_viewer_the_machine_and_the_mode() -> None:
    ticket = mint_ticket(provider_id="sbx-1", sub="u:pane", mode="view", now=1000.0)
    claims = _verify(ticket, stream_key("sbx-1"))
    assert claims["sub"] == "u:pane"
    assert claims["machine"] == "sbx-1"
    assert claims["mode"] == "view"
    assert claims["exp"] == 1000 + TICKET_TTL_SECONDS
    assert isinstance(claims["jti"], str) and claims["jti"]


def test_two_tickets_are_never_the_same_ticket() -> None:
    """The streamer refuses a jti it has seen, so a copied ticket is spent."""
    first = mint_ticket(provider_id="sbx-1", sub="u", mode="view", now=1.0)
    second = mint_ticket(provider_id="sbx-1", sub="u", mode="view", now=1.0)
    assert first != second


def test_a_machines_key_signs_only_for_that_machine() -> None:
    """A key read out of one sandbox forges nothing for another."""
    ticket = mint_ticket(provider_id="sbx-1", sub="u", mode="control")
    with pytest.raises(AssertionError):
        _verify(ticket, stream_key("sbx-2"))


def test_flipping_the_mode_breaks_the_signature() -> None:
    ticket = mint_ticket(provider_id="sbx-1", sub="u", mode="view")
    payload, _, signature = ticket.partition(".")
    claims = json.loads(_decode(payload))
    claims["mode"] = "control"
    forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    with pytest.raises(AssertionError):
        _verify(f"{forged}.{signature}", stream_key("sbx-1"))


def test_the_key_file_carries_the_key_and_the_machine_id() -> None:
    body = json.loads(stream_key_file("sbx-1"))
    assert body == {"key": stream_key("sbx-1"), "machine": "sbx-1"}


def test_the_key_is_derived_so_redelivery_is_idempotent() -> None:
    assert stream_key("sbx-1") == stream_key("sbx-1")
    assert stream_key("sbx-1") != stream_key("sbx-2")


# --- who gets WebRTC -------------------------------------------------------


def test_nobody_gets_webrtc_without_a_turn_relay(monkeypatch) -> None:
    monkeypatch.setattr(workspace_settings, "turn_provider", "none")
    monkeypatch.setattr(workspace_settings, "browser_stream", "auto")
    monkeypatch.setattr(workspace_settings, "browser_stream_percent", 100)
    assert transport_for(uuid4()) == "vnc"


def test_the_team_list_is_in_and_everyone_else_waits(monkeypatch) -> None:
    team = uuid4()
    monkeypatch.setattr(workspace_settings, "turn_provider", "coturn")
    monkeypatch.setattr(workspace_settings, "browser_stream", "auto")
    monkeypatch.setattr(workspace_settings, "browser_stream_users", [str(team)])
    monkeypatch.setattr(workspace_settings, "browser_stream_percent", 0)
    assert transport_for(team) == "auto"
    assert transport_for(uuid4()) == "vnc"


def test_a_percentage_is_a_stable_share(monkeypatch) -> None:
    monkeypatch.setattr(workspace_settings, "turn_provider", "cloudflare")
    monkeypatch.setattr(workspace_settings, "browser_stream", "auto")
    monkeypatch.setattr(workspace_settings, "browser_stream_users", [])
    monkeypatch.setattr(workspace_settings, "browser_stream_percent", 10)
    people = [UUID(int=n) for n in range(2000)]
    first = [transport_for(person) for person in people]
    assert first == [transport_for(person) for person in people]
    share = first.count("auto") / len(people)
    assert 0.05 < share < 0.15


def test_the_server_only_allows_density_the_machine_decides(monkeypatch) -> None:
    """The flag reaches the machine as a file; its CPUs are its own business."""
    monkeypatch.setattr(workspace_settings, "browser_native_density", True)
    assert json.loads(display_decision()) == {"native_density": True}
    monkeypatch.setattr(workspace_settings, "browser_native_density", False)
    assert json.loads(display_decision()) == {"native_density": False}


# --- TURN ------------------------------------------------------------------


async def test_coturn_credentials_are_the_rest_scheme() -> None:
    servers = await Coturn(
        ["turn:turn.test:3478", "turns:turn.test:443?transport=tcp"],
        "secret",
        now=lambda: 1_000.0,
    ).ice_servers(label="viewer-u", ttl_seconds=600)
    (server,) = servers
    # Built from the inputs, so the check does not just echo the code's answer.
    login = b"1600:viewer-u"
    assert server["username"] == login.decode()
    expected = base64.b64encode(hmac.new(b"secret", login, hashlib.sha1).digest())
    assert server["credential"] == expected.decode()


def test_tls_443_is_added_where_a_provider_left_it_out() -> None:
    servers = with_tls_443(
        [
            {"urls": ["stun:stun.test:3478"]},
            {
                "urls": ["turn:t.test:3478", "turn:t.test:53"],
                "username": "u",
                "credential": "c",
            },
        ],
        "turns:t.test:443?transport=tcp",
    )
    assert servers == [
        {
            "urls": ["turn:t.test:3478", "turns:t.test:443?transport=tcp"],
            "username": "u",
            "credential": "c",
        }
    ]


def test_both_of_cloudflares_answer_shapes_are_read() -> None:
    listed = _cloudflare_servers(
        {"iceServers": [{"urls": ["turn:a"], "username": "u", "credential": "c"}]}
    )
    single = _cloudflare_servers(
        {"iceServers": {"urls": "turn:a", "username": "u", "credential": "c"}}
    )
    assert (
        listed == single == [{"urls": ["turn:a"], "username": "u", "credential": "c"}]
    )
    assert _cloudflare_servers({"nonsense": 1}) == []
    assert _cloudflare_servers([1, 2]) == []


def test_no_provider_means_no_webrtc(monkeypatch) -> None:
    monkeypatch.setattr(workspace_settings, "turn_provider", "coturn")
    monkeypatch.setattr(workspace_settings, "coturn_urls", [])
    assert browser_stream_turn.turn_provider() is None
    monkeypatch.setattr(workspace_settings, "turn_provider", "none")
    assert browser_stream_turn.turn_provider() is None


# --- what the browser may send ---------------------------------------------

_VIEWER = RtcViewer(sub="u:p", provider_id="sbx-1", mode="view", may_control=True)
_WATCH_ONLY = RtcViewer(sub="u:p", provider_id="sbx-1", mode="view", may_control=False)


def test_an_offer_and_a_candidate_pass_with_only_their_own_fields() -> None:
    offer = json.loads(
        from_browser(json.dumps({"t": "offer", "sdp": "v=0", "x": 1}), _VIEWER) or ""
    )
    assert offer == {"t": "offer", "sdp": "v=0"}
    candidate = json.loads(
        from_browser(
            json.dumps(
                {"t": "candidate", "candidate": "c", "sdpMid": "0", "sdpMLineIndex": 0}
            ),
            _VIEWER,
        )
        or ""
    )
    assert candidate == {
        "t": "candidate",
        "candidate": "c",
        "sdpMid": "0",
        "sdpMLineIndex": 0,
    }


@pytest.mark.parametrize(
    "message",
    [
        {"t": "hello", "ticket": "forged"},
        {"t": "control", "ticket": "forged"},
        {"t": "exec", "cmd": "id"},
        {"t": "offer"},
        {"t": "offer", "sdp": 7},
        {"t": "candidate", "sdpMLineIndex": "0"},
        {"t": "candidate", "sdpMLineIndex": True},
        ["t", "offer"],
        "offer",
    ],
)
def test_a_ticket_or_anything_else_never_comes_from_the_browser(message) -> None:
    assert from_browser(json.dumps(message), _VIEWER) is None


def test_garbage_and_oversize_are_dropped() -> None:
    assert from_browser("{not json", _VIEWER) is None
    huge = json.dumps({"t": "offer", "sdp": "x" * 70_000})
    assert from_browser(huge, _VIEWER) is None


def test_asking_to_drive_becomes_a_control_ticket_only_if_allowed() -> None:
    granted = json.loads(from_browser(json.dumps({"t": "take"}), _VIEWER) or "")
    assert granted["t"] == "control"
    claims = _verify(granted["ticket"], stream_key("sbx-1"))
    assert claims["mode"] == "control" and claims["sub"] == "u:p"
    assert from_browser(json.dumps({"t": "take"}), _WATCH_ONLY) is None


def test_giving_back_and_leaving_pass_as_themselves() -> None:
    assert json.loads(from_browser('{"t":"release"}', _VIEWER) or "") == {
        "t": "release"
    }
    assert json.loads(from_browser('{"t":"bye"}', _VIEWER) or "") == {"t": "bye"}


def test_the_browser_is_never_handed_a_ticket() -> None:
    """`config` -- the only message this side writes to the browser itself --
    carries ICE servers and the mode; `hello`, which goes to the streamer,
    carries the ticket."""
    servers = [{"urls": ["turn:t.test:3478"], "username": "u", "credential": "c"}]
    config = json.loads(config_message(_VIEWER, servers))
    assert config == {
        "t": "config",
        "ice_servers": servers,
        "policy": "relay",
        "mode": "view",
        "may_control": True,
    }
    hello = json.loads(hello_message(_VIEWER, servers))
    assert _verify(hello["ticket"], stream_key("sbx-1"))["mode"] == "view"
    assert hello["policy"] == "relay"
