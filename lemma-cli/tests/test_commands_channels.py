from __future__ import annotations

import json
from types import SimpleNamespace

from lemma_cli.cli_core.app import app
from lemma_cli.cli_core.commands import channel_lines, channels


class Recorder:
    """Every SDK call made, by name, with what it was given."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return {"called": name}

        return call


def _client(
    channels_fake: Recorder,
    sources_fake: Recorder | None = None,
    contacts_fake: Recorder | None = None,
):
    channels_fake.sources = sources_fake or Recorder()

    class FakeClient:
        def pod(self, pod_id):
            return SimpleNamespace(
                channels=channels_fake,
                contacts=contacts_fake or Recorder(),
            )

    return FakeClient()


def test_list_reads_the_kind_in_any_case(runner, patch_run, json_state):
    fake = Recorder()
    patch_run(channels, client=_client(fake), state=json_state)

    result = runner.invoke(app, ["channels", "list", "--kind", "group", "--json"])

    assert result.exit_code == 0, result.stderr
    [(name, _, kwargs)] = fake.calls
    assert name == "list"
    assert kwargs["kind"] == "GROUP"


def test_an_unknown_kind_is_refused_before_anything_is_asked(
    runner, patch_run, json_state
):
    fake = Recorder()
    patch_run(channels, client=_client(fake), state=json_state)

    result = runner.invoke(app, ["channels", "list", "--kind", "room"])

    assert result.exit_code != 0
    assert fake.calls == []


def test_post_names_the_agents_and_members_it_mentions(runner, patch_run, json_state):
    fake = Recorder()
    patch_run(channel_lines, client=_client(fake), state=json_state)

    result = runner.invoke(
        app,
        [
            "channels",
            "messages",
            "post",
            "ch-1",
            "Can you summarise?",
            "--mention-agent",
            "agent-1",
            "--mention",
            "user-2",
            "--thread",
            "line-9",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stderr
    [(name, args, kwargs)] = fake.calls
    assert (name, args) == ("post", ("ch-1", "Can you summarise?"))
    assert kwargs["mentions"] == [
        {"kind": "AGENT", "id": "agent-1"},
        {"kind": "MEMBER", "id": "user-2"},
    ]
    assert kwargs["thread_root_id"] == "line-9"
    assert kwargs["members_only"] is False
    assert kwargs["client_message_id"] is None


def test_an_answer_needs_something_to_say(runner, patch_run, json_state):
    fake = Recorder()
    patch_run(channel_lines, client=_client(fake), state=json_state)

    refused = runner.invoke(app, ["channels", "messages", "answer", "ch-1", "line-1"])
    answered = runner.invoke(
        app,
        [
            "channels",
            "messages",
            "answer",
            "ch-1",
            "line-1",
            "-d",
            json.dumps({"seats": 3}),
        ],
    )

    assert refused.exit_code != 0
    assert answered.exit_code == 0, answered.stderr
    assert fake.calls == [("answer", ("ch-1", "line-1", {"seats": 3}), {})]


def test_a_source_is_switched_under_its_channel(runner, patch_run, json_state):
    sources = Recorder()
    patch_run(channels, client=_client(Recorder(), sources), state=json_state)

    switched = runner.invoke(
        app,
        ["channels", "source", "set", "ch-1", "src-1", "--no-answers-outsiders"],
    )
    taken = runner.invoke(
        app, ["channels", "source", "set", "ch-1", "src-1", "--take-over", "--json"]
    )
    nothing = runner.invoke(app, ["channels", "source", "set", "ch-1", "src-1"])
    listed = runner.invoke(app, ["channels", "source", "list", "acme-bot", "--json"])

    assert switched.exit_code == 0, switched.stderr
    assert taken.exit_code == 0, taken.stderr
    assert nothing.exit_code != 0
    assert listed.exit_code == 0, listed.stderr
    assert sources.calls == [
        (
            "update",
            ("ch-1", "src-1"),
            {"take_over": False, "answers_outsiders": False},
        ),
        ("update", ("ch-1", "src-1"), {"take_over": True}),
        ("list", ("acme-bot",), {"limit": 50, "cursor": None}),
    ]


def test_a_contacts_agent_is_switched_on_or_off(runner, patch_run, json_state):
    fake = Recorder()
    patch_run(channels, client=_client(fake), state=json_state)

    off = runner.invoke(app, ["channels", "agent", "ch-1", "off", "--json"])
    on = runner.invoke(
        app,
        ["channels", "agent", "ch-1", "on", "--hand-back-after", "6", "--json"],
    )
    wrong = runner.invoke(app, ["channels", "agent", "ch-1", "maybe"])

    assert off.exit_code == 0, off.stderr
    assert on.exit_code == 0, on.stderr
    assert wrong.exit_code != 0
    assert fake.calls == [
        ("set_agent_answers", ("ch-1", False), {}),
        ("set_agent_answers", ("ch-1", True), {"hand_back_after_hours": 6}),
    ]


def test_an_update_sends_only_what_was_named(runner, patch_run, json_state):
    fake = Recorder()
    patch_run(channels, client=_client(fake), state=json_state)

    result = runner.invoke(
        app,
        [
            "channels",
            "update",
            "ch-1",
            "--clear-topic",
            "--assignee",
            "none",
            "--status",
            "CLOSED",
            "--json",
        ],
    )
    nothing = runner.invoke(app, ["channels", "update", "ch-1"])

    assert result.exit_code == 0, result.stderr
    assert nothing.exit_code != 0
    assert fake.calls == [
        ("update", ("ch-1",), {"topic": None, "assignee": None, "status": "closed"})
    ]


def test_a_member_is_named_by_kind_and_id(runner, patch_run, json_state):
    fake = Recorder()
    patch_run(channels, client=_client(fake), state=json_state)

    result = runner.invoke(
        app,
        [
            "channels",
            "members",
            "update",
            "ch-1",
            "agent:a-1",
            "--answers",
            "every_line",
            "--json",
        ],
    )
    unnamed = runner.invoke(
        app, ["channels", "members", "update", "ch-1", "a-1", "--answers", "MENTIONED"]
    )

    assert result.exit_code == 0, result.stderr
    assert unnamed.exit_code != 0
    assert fake.calls == [
        (
            "update_member",
            ("ch-1", "AGENT", "a-1"),
            {"role": None, "answers": "EVERY_LINE"},
        )
    ]


def test_notify_levels_go_to_your_own_membership(runner, patch_run, json_state):
    fake = Recorder()
    patch_run(channels, client=_client(fake), state=json_state)

    result = runner.invoke(
        app,
        ["channels", "notify", "ch-1", "mentions", "--pod-rule", "--unmute", "--json"],
    )

    assert result.exit_code == 0, result.stderr
    assert fake.calls == [
        (
            "set_notify",
            ("ch-1",),
            {"notify": "MENTIONS", "via": None, "muted_until": None},
        )
    ]


def test_read_marks_up_to_the_given_line(runner, patch_run, json_state):
    fake = Recorder()
    patch_run(channels, client=_client(fake), state=json_state)

    result = runner.invoke(app, ["channels", "read", "ch-1", "--seq", "12", "--json"])

    assert result.exit_code == 0, result.stderr
    assert fake.calls == [("mark_read", ("ch-1", 12), {})]


def test_leaving_needs_only_the_channel(runner, patch_run, json_state):
    fake = Recorder()
    patch_run(channels, client=_client(fake), state=json_state)

    result = runner.invoke(app, ["channels", "leave", "ch-1", "--json"])

    assert result.exit_code == 0, result.stderr
    assert fake.calls == [("leave", ("ch-1",), {})]


def test_outreach_writes_to_a_contact_on_the_named_handle(
    runner, patch_run, json_state
):
    contacts_fake = Recorder()
    patch_run(
        channels,
        client=_client(Recorder(), contacts_fake=contacts_fake),
        state=json_state,
    )

    result = runner.invoke(
        app,
        [
            "channels",
            "outreach",
            "c-1",
            "ana@example.com",
            "Your order shipped.",
            "--client-message-id",
            "k1",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stderr
    [(name, args, kwargs)] = contacts_fake.calls
    assert (name, args) == ("outreach", ("c-1",))
    assert kwargs == {
        "handle": "ana@example.com",
        "text": "Your order shipped.",
        "kind": None,
        "client_message_id": "k1",
    }
