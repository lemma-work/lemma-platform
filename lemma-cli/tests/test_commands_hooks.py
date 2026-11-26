from __future__ import annotations

import json

from lemma_cli.cli_core.app import app
from lemma_cli.cli_core.commands import hooks

HOOK = {
    "id": "h-1",
    "name": "typeform",
    "table": "form_submissions",
    "enabled": True,
    "fields": None,
}
ISSUED = {
    **HOOK,
    "secret": "lmh_s",
    "url": "https://api.example.com/webhooks/hook?hook=h-1&key=lmh_s",
}


class FakePod:
    pod_id = "pod-1"

    def __init__(self, answer=None) -> None:
        self.calls: list[tuple[str, str, object]] = []
        self.answer = answer if answer is not None else HOOK

    def request(self, method, path, *, json=None):
        self.calls.append((method, path, json))
        return self.answer


def _client(pod: FakePod):
    class FakeClient:
        def pod(self, pod_id):
            return pod

    return FakeClient()


def test_create_sends_the_table_and_fields_and_says_the_url_is_shown_once(
    runner, patch_run, json_state
):
    pod = FakePod(ISSUED)
    patch_run(hooks, client=_client(pod), state=json_state)

    result = runner.invoke(
        app,
        [
            "hook",
            "create",
            "tally",
            "--table",
            "leads",
            "-F",
            "email=data.fields.0.value",
            "--field",
            "raw=$",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert pod.calls == [
        (
            "POST",
            "/pods/pod-1/datastore/hooks",
            {
                "name": "tally",
                "table": "leads",
                "fields": {"email": "data.fields.0.value", "raw": "$"},
            },
        )
    ]
    assert json.loads(result.stdout)["url"] == ISSUED["url"]
    assert "not shown again" in result.stderr


def test_create_needs_a_table_and_well_formed_fields(runner, patch_run, json_state):
    pod = FakePod()
    patch_run(hooks, client=_client(pod), state=json_state)

    no_table = runner.invoke(app, ["hook", "create", "x"])
    bad_field = runner.invoke(app, ["hook", "create", "x", "-t", "t", "-F", "email"])

    assert no_table.exit_code == 2
    assert bad_field.exit_code == 2
    assert pod.calls == []


def test_enable_disable_and_update_patch_the_hook(runner, patch_run, json_state):
    pod = FakePod()
    patch_run(hooks, client=_client(pod), state=json_state)

    for args in (
        ["hook", "disable", "typeform"],
        ["hook", "enable", "typeform"],
        ["hook", "update", "typeform", "-F", "email=e"],
        ["hook", "update", "typeform", "--whole-body"],
    ):
        assert runner.invoke(app, args).exit_code == 0

    assert pod.calls == [
        ("PATCH", "/pods/pod-1/datastore/hooks/typeform", {"enabled": False}),
        ("PATCH", "/pods/pod-1/datastore/hooks/typeform", {"enabled": True}),
        ("PATCH", "/pods/pod-1/datastore/hooks/typeform", {"fields": {"email": "e"}}),
        ("PATCH", "/pods/pod-1/datastore/hooks/typeform", {"fields": None}),
    ]


def test_rotate_and_delete_refuse_without_yes_when_nobody_can_answer(
    runner, patch_run, json_state
):
    pod = FakePod(ISSUED)
    patch_run(hooks, client=_client(pod), state=json_state)

    refused = runner.invoke(app, ["hook", "rotate", "typeform"])
    rotated = runner.invoke(app, ["hook", "rotate", "typeform", "--yes"])
    deleted = runner.invoke(app, ["hook", "delete", "typeform", "-y"])

    assert refused.exit_code == 1
    assert rotated.exit_code == 0, rotated.output
    assert "not shown again" in rotated.stderr
    assert deleted.exit_code == 0, deleted.output
    assert pod.calls == [
        ("POST", "/pods/pod-1/datastore/hooks/typeform/rotate", None),
        ("DELETE", "/pods/pod-1/datastore/hooks/typeform", None),
    ]


def test_list_takes_limit(runner, patch_run, json_state):
    pod = FakePod({"items": [HOOK, {**HOOK, "name": "tally"}]})
    patch_run(hooks, client=_client(pod), state=json_state)

    result = runner.invoke(app, ["hooks", "list", "--limit", "1", "--json"])

    assert result.exit_code == 0, result.output
    assert [item["name"] for item in json.loads(result.stdout)["items"]] == ["typeform"]
    assert pod.calls == [("GET", "/pods/pod-1/datastore/hooks", None)]
