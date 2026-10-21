from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx
import pytest

from lemma_sdk import Lemma
from lemma_sdk.errors import LemmaInvalidIdError
from lemma_sdk.openapi_client.api.channels import (
    channel_list,
    channel_member_leave,
    channel_member_list,
    channel_message_post,
    channel_read,
    channel_search,
    channel_update,
)
from lemma_sdk.openapi_client.models.channel_kind import ChannelKind
from lemma_sdk.openapi_client.models.sender_kind import SenderKind
from lemma_sdk.openapi_client.models.visibility import Visibility
from lemma_sdk.resources.channels import PodChannels

POD = uuid4()
CHANNEL = uuid4()


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, tuple[Any, ...], dict[str, Any]]] = []

    def call(self, endpoint: Any, *path_args: Any, **kwargs: Any) -> Any:
        self.calls.append((endpoint, path_args, kwargs))
        return None


def _sent(call: tuple[Any, tuple[Any, ...], dict[str, Any]]) -> dict[str, Any]:
    """The body as it would go over the wire, through the generated model."""
    _, _, kwargs = call
    return kwargs["body_model"].from_dict(kwargs["body"]).to_dict()


def test_an_update_sends_only_the_fields_named_and_none_clears() -> None:
    transport = RecordingTransport()

    PodChannels(transport, pod_id=POD).update(CHANNEL, topic=None, archived=True)

    [call] = transport.calls
    assert call[0] is channel_update
    assert call[1] == (POD, CHANNEL)
    assert _sent(call) == {"topic": None, "archived": True}


def test_a_group_keeping_answers_to_the_asker_is_a_patch_of_answers_shared() -> None:
    transport = RecordingTransport()

    PodChannels(transport, pod_id=POD).update(CHANNEL, answers_shared=False)

    [call] = transport.calls
    assert _sent(call) == {"answers_shared": False}


def test_switching_a_contacts_agent_is_a_patch_of_agents_answer() -> None:
    transport = RecordingTransport()
    channels = PodChannels(transport, pod_id=POD)

    channels.set_agent_answers(CHANNEL, False)
    channels.set_agent_answers(CHANNEL, True, hand_back_after_hours=6)

    assert [call[0] for call in transport.calls] == [channel_update, channel_update]
    assert _sent(transport.calls[0]) == {"agents_answer": False}
    assert _sent(transport.calls[1]) == {
        "agents_answer": True,
        "hand_back_after_hours": 6,
    }


def test_read_is_marked_up_to_a_seq() -> None:
    transport = RecordingTransport()

    PodChannels(transport, pod_id=POD).mark_read(CHANNEL, 42)

    [call] = transport.calls
    assert call[0] is channel_read
    assert _sent(call) == {"seq": 42}


def test_mentions_go_out_as_typed_parts() -> None:
    transport = RecordingTransport()
    agent = str(uuid4())

    PodChannels(transport, pod_id=POD).post(
        CHANNEL,
        "Can you summarise?",
        mentions=[{"kind": "AGENT", "id": agent}],
        client_message_id="draft-1",
    )

    [call] = transport.calls
    assert call[0] is channel_message_post
    sent = _sent(call)
    assert sent["parts"] == [{"type": "mention", "kind": "AGENT", "id": agent}]
    assert sent["client_message_id"] == "draft-1"


def test_leaving_is_one_call_about_the_caller() -> None:
    transport = RecordingTransport()

    PodChannels(transport, pod_id=POD).leave(CHANNEL)

    [(endpoint, path_args, _)] = transport.calls
    assert endpoint is channel_member_leave
    assert path_args == (POD, CHANNEL)


def test_a_contact_is_assigned_to_me_and_an_edit_clears_parts() -> None:
    transport = RecordingTransport()
    channels = PodChannels(transport, pod_id=POD)

    channels.update(CHANNEL, assignee="me")
    channels.edit_message(CHANNEL, uuid4(), parts=None)

    assert _sent(transport.calls[0]) == {"assignee": "me"}
    assert _sent(transport.calls[1]) == {"parts": None}


def test_a_groups_source_is_under_its_channel() -> None:
    from lemma_sdk.openapi_client.api.channel_sources import (
        channel_source_update,
        channel_whatsapp_group_start,
    )
    from lemma_sdk.openapi_client.api.channels import channel_list

    transport = RecordingTransport()
    sources = PodChannels(transport, pod_id=POD).sources
    source = uuid4()

    sources.update(CHANNEL, source, take_over=True)
    sources.list("acme-whatsapp")
    sources.start_whatsapp_group("acme-whatsapp", "Order 1182", idempotency_key="k")

    update, listed, started = transport.calls
    assert (update[0], update[1]) == (channel_source_update, (POD, CHANNEL, source))
    assert _sent(update) == {"owner": "me"}
    assert listed[0] is channel_list
    assert listed[2]["source_surface"] == "acme-whatsapp"
    assert started[0] is channel_whatsapp_group_start
    assert started[2]["idempotency_key"] == "k"


def test_a_channels_members_are_read_a_page_at_a_time() -> None:
    transport = RecordingTransport()

    PodChannels(transport, pod_id=POD).members(CHANNEL, cursor="next", limit=2)

    [(endpoint, path_args, kwargs)] = transport.calls
    assert endpoint is channel_member_list
    assert path_args == (POD, CHANNEL)
    assert (kwargs["cursor"], kwargs["limit"]) == ("next", 2)


def test_search_narrows_by_who_kind_and_when() -> None:
    transport = RecordingTransport()
    who, since = uuid4(), datetime(2026, 10, 1, tzinfo=timezone.utc)

    PodChannels(transport, pod_id=POD).search(
        "invoice", kind="GROUP", sender=("MEMBER", who), since=since, archived=True
    )

    [(endpoint, _, kwargs)] = transport.calls
    assert endpoint is channel_search
    assert kwargs["kind"] is ChannelKind.GROUP
    assert (kwargs["sender_kind"], kwargs["sender_id"]) == (SenderKind.MEMBER, who)
    assert (kwargs["since"], kwargs["archived"]) == (since, True)


def test_the_channel_list_narrows_by_visibility_and_title() -> None:
    transport = RecordingTransport()

    PodChannels(transport, pod_id=POD).list(visibility="PRIVATE", q="launch")

    [(endpoint, _, kwargs)] = transport.calls
    assert endpoint is channel_list
    assert (kwargs["visibility"], kwargs["q"]) == (Visibility.PRIVATE, "launch")


# -- over the wire ---------------------------------------------------------------
#
# The tests above read the body back through the generated model. These run the
# real HTTP layer -- the generated client, the transport, httpx -- against a
# mocked server, so what is asserted is what a server would receive and what a
# caller gets back.


def _wired_channels(handler: Any) -> tuple[Any, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    lemma = Lemma(token="token", base_url="https://api.example.test")
    lemma._transport.generated.set_httpx_client(
        httpx.Client(
            transport=httpx.MockTransport(record),
            base_url="https://api.example.test",
        )
    )
    return lemma.pod(str(POD)).channels, seen


def _channel_json() -> dict[str, Any]:
    stamp = "2026-10-20T10:00:00Z"
    return {
        "id": str(CHANNEL),
        "kind": "GROUP",
        "visibility": "PUBLIC",
        "created_at": stamp,
        "last_line_at": stamp,
    }


def test_an_update_with_none_sends_an_explicit_null_on_the_wire() -> None:
    channels, seen = _wired_channels(
        lambda _: httpx.Response(200, json=_channel_json())
    )

    channels.update(CHANNEL, topic=None, title="Launch")

    [request] = seen
    assert request.method == "PATCH"
    assert request.url.path == f"/pods/{POD}/channels/{CHANNEL}"
    assert json.loads(request.content) == {"title": "Launch", "topic": None}


def test_a_shared_file_comes_back_as_its_bytes() -> None:
    png = b"\x89PNG\r\n\x1a\n\x00\x00binary\xff"
    channels, seen = _wired_channels(
        lambda _: httpx.Response(
            200, headers={"content-type": "image/png"}, content=png
        )
    )
    message, file_id = uuid4(), uuid4()

    data = channels.file(CHANNEL, message, file_id)

    assert isinstance(data, bytes)
    assert data == png
    assert seen[0].url.path == (
        f"/pods/{POD}/channels/{CHANNEL}/messages/{message}/files/{file_id}"
    )


def test_a_malformed_id_is_refused_by_name_before_any_request() -> None:
    channels, seen = _wired_channels(lambda _: httpx.Response(500))

    with pytest.raises(LemmaInvalidIdError) as raised:
        channels.messages("not-a-channel")

    assert "'not-a-channel' is not an id" in str(raised.value)
    assert "badly formed" not in str(raised.value)
    assert isinstance(raised.value, ValueError)
    assert seen == []


def test_sharing_an_answer_posts_to_its_share_route() -> None:
    message = uuid4()
    stamp = "2026-10-20T10:00:00Z"
    line = {
        "id": str(message),
        "channel_id": str(CHANNEL),
        "seq": 3,
        "sender": {"kind": "AGENT", "id": str(POD), "name": "Bolt"},
        "text": "Within 30 days.",
        "visibility": "channel",
        "shared_at": stamp,
        "created_at": stamp,
    }
    channels, seen = _wired_channels(lambda _: httpx.Response(200, json=line))

    shared = channels.share(CHANNEL, message)

    [request] = seen
    assert request.method == "POST"
    assert request.url.path == (
        f"/pods/{POD}/channels/{CHANNEL}/messages/{message}/share"
    )
    assert shared.text == "Within 30 days."


def test_sharing_someone_elses_answer_raises() -> None:
    channels, _ = _wired_channels(
        lambda _: httpx.Response(
            403,
            json={
                "message": "Only the member an answer was made for may share it",
                "code": "INSUFFICIENT_PERMISSION",
                "request_id": "r-1",
                "details": None,
            },
        )
    )

    with pytest.raises(Exception) as raised:
        channels.share(CHANNEL, uuid4())

    assert "may share it" in str(raised.value)
