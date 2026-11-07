"""`lemma pods templates|start|owns`: starting a Lemma and what it owns."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from rich.console import Console
from typer.testing import CliRunner

from lemma_cli.cli_core.app import app
from lemma_cli.cli_core.commands import pods

pytestmark = pytest.mark.unit

runner = CliRunner()

POD_UUID = "11111111-1111-1111-1111-111111111111"
ORG_UUID = "99999999-9999-9999-9999-999999999999"
CONVERSATION_UUID = "22222222-2222-2222-2222-222222222222"

SUPPORT_CARD = {
    "id": "support",
    "name": "Support",
    "sentence": "Every customer hears back within a day.",
    "kind": "week",
    "brings": ["conversations"],
    "outcome": {
        "sentence": "Every customer hears back within a day.",
        "kind": "week",
        "target": 90,
        "unit": "%",
        "measure": "SELECT 1",
    },
    "plan": {"how": [], "never": []},
    "position": 1,
}

OUTCOME = {
    "sentence": "Every customer hears back within a day.",
    "kind": "week",
    "target": 90.0,
    "unit": "%",
    "measure": "SELECT 87.5",
    "since": "2026-11-02",
    "row_security_checked": [],
}


class FakePods:
    def __init__(self, standing=None):
        self.calls: dict[str, object] = {}
        self._standing = standing or {
            "outcome": OUTCOME,
            "value": 87.5,
            "measured_at": "2026-11-07T09:00:00Z",
            "problem": None,
        }

    def start(self, request):
        self.calls["start"] = request.to_dict()
        return {
            "pod": {"id": POD_UUID, "organization_id": ORG_UUID, "name": "Support"},
            "conversation_id": CONVERSATION_UUID,
            "import_id": None,
        }

    def outcome_standing(self, pod_id):
        self.calls["standing"] = pod_id
        return self._standing

    def outcome_history(self, pod_id, *, weeks=8):
        self.calls["history"] = (pod_id, weeks)
        return {
            "weeks": [
                {"starts_on": "2026-10-26", "value": 80.0, "target": 90.0},
                {"starts_on": "2026-11-02", "value": 87.5, "target": 90.0},
            ]
        }

    def set_outcome(self, pod_id, request):
        self.calls["set"] = (pod_id, request.to_dict())
        return {"outcome": request.to_dict(), "value": 87.5, "measured_at": None}

    def clear_outcome(self, pod_id):
        self.calls["clear"] = pod_id


def _generated_answering(status: int, body: object) -> SimpleNamespace:
    """The generated client, answering every request with one response."""
    response = httpx.Response(status, json=body)
    return SimpleNamespace(
        get_httpx_client=lambda: SimpleNamespace(request=lambda **_: response),
        raise_on_unexpected_status=False,
    )


@pytest.fixture
def fake(monkeypatch):
    fake_pods = FakePods()
    client = SimpleNamespace(
        pods=fake_pods, generated=_generated_answering(200, [SUPPORT_CARD])
    )

    def fake_run_with_client(ctx, fn):
        state = SimpleNamespace(
            config={"defaults": {"org_id": ORG_UUID, "pod_id": POD_UUID}},
            output="pretty",
            server="local",
        )
        return fn(client, state)

    monkeypatch.setattr(pods, "run_with_client", fake_run_with_client)
    return client


# -- templates ---------------------------------------------------------------------


def test_templates_lists_each_card_with_what_it_owns(fake, monkeypatch):
    # Wide enough that no cell folds, so the row reads as one line.
    monkeypatch.setattr(pods, "console", Console(width=200))

    result = runner.invoke(app, ["pods", "templates"])

    assert result.exit_code == 0, result.output
    assert "support" in result.stdout
    assert "Every customer hears back within a day." in result.stdout
    assert "90% every week" in result.stdout


def test_templates_json_is_the_cards(fake):
    result = runner.invoke(app, ["--json", "pods", "templates"])

    assert result.exit_code == 0, result.output
    cards = json.loads(result.stdout)
    assert [card["id"] for card in cards] == ["support"]
    assert cards[0]["outcome"]["target"] == 90


def test_templates_refusal_is_reported_not_parsed(monkeypatch):
    client = SimpleNamespace(
        generated=_generated_answering(403, {"detail": "Not allowed"})
    )
    seen: list[BaseException] = []

    def fake_run_with_client(ctx, fn):
        try:
            return fn(client, SimpleNamespace(config={}, output="pretty"))
        except Exception as exc:  # noqa: BLE001 — what run_with_client would see
            seen.append(exc)
            raise SystemExit(1) from exc

    monkeypatch.setattr(pods, "run_with_client", fake_run_with_client)

    result = runner.invoke(app, ["pods", "templates"])

    assert result.exit_code == 1
    assert getattr(seen[0], "status_code", None) == 403
    assert "Not allowed" in str(seen[0])


# -- start ---------------------------------------------------------------------------


def test_start_from_a_template_sends_template_due_and_org(fake):
    result = runner.invoke(
        app,
        [
            "pods",
            "start",
            "--template",
            "launch",
            "--due",
            "2026-12-01",
            "--name",
            "Spring",
        ],
    )

    assert result.exit_code == 0, result.output
    sent = fake.pods.calls["start"]
    assert sent["organization_id"] == ORG_UUID
    assert sent["template"] == "launch"
    assert sent["due"] == "2026-12-01"
    assert sent["name"] == "Spring"
    assert "outcome" not in sent
    # The ids are printed, and the hint names a command that follows the chat.
    assert POD_UUID in result.stdout and CONVERSATION_UUID in result.stdout
    assert f"lemma conversations stream {CONVERSATION_UUID} --pod {POD_UUID}" in (
        result.output
    )


def test_start_from_own_words_sends_the_outcome(fake):
    result = runner.invoke(
        app,
        [
            "--json",
            "pods",
            "start",
            "--name",
            "Replies",
            "--owns",
            "Every customer hears back within a day",
            "--kind",
            "week",
            "--target",
            "90",
            "--unit",
            "%",
        ],
    )

    assert result.exit_code == 0, result.output
    sent = fake.pods.calls["start"]
    assert sent["name"] == "Replies"
    assert "template" not in sent
    assert sent["outcome"] == {
        "sentence": "Every customer hears back within a day",
        "kind": "week",
        "target": 90.0,
        "unit": "%",
    }
    assert json.loads(result.stdout)["conversation_id"] == CONVERSATION_UUID


@pytest.mark.parametrize(
    "argv",
    [
        ["pods", "start"],
        ["pods", "start", "--template", "support", "--owns", "x y z", "--name", "n"],
        ["pods", "start", "--owns", "x y z", "--name", "n"],
        ["pods", "start", "--owns", "x y z", "--kind", "week"],
        ["pods", "start", "--owns", "x y z", "--kind", "month", "--name", "n"],
        ["pods", "start", "--owns", "x y z", "--kind", "date", "--name", "n"],
        [
            "pods",
            "start",
            "--owns",
            "x y z",
            "--kind",
            "week",
            "--name",
            "n",
            "--due",
            "2026-12-01",
        ],
        ["pods", "start", "--template", "launch", "--due", "December"],
        ["pods", "start", "--template", "support", "--target", "5"],
    ],
)
def test_start_refuses_a_shape_the_api_would_refuse(fake, argv):
    result = runner.invoke(app, argv)

    assert result.exit_code == 2, result.output
    assert "start" not in fake.pods.calls


# -- owns ----------------------------------------------------------------------------


def test_owns_shows_what_it_owns_and_the_number_now(fake):
    result = runner.invoke(app, ["pods", "owns"])

    assert result.exit_code == 0, result.output
    assert fake.pods.calls["standing"] == POD_UUID
    assert "history" not in fake.pods.calls
    assert "Every customer hears back within a day." in result.stdout
    assert "87.5%" in result.stdout
    assert "SELECT 87.5" in result.stdout


def test_owns_get_with_history_adds_the_weeks(fake):
    result = runner.invoke(
        app, ["--json", "pods", "owns", "get", POD_UUID, "--history", "2"]
    )

    assert result.exit_code == 0, result.output
    assert fake.pods.calls["history"] == (POD_UUID, 2)
    payload = json.loads(result.stdout)
    assert payload["value"] == 87.5
    assert payload["outcome"]["sentence"] == OUTCOME["sentence"]
    assert [week["starts_on"] for week in payload["weeks"]] == [
        "2026-10-26",
        "2026-11-02",
    ]


def test_owns_says_when_the_pod_owns_nothing(fake):
    fake.pods._standing = {"outcome": None, "value": None, "problem": None}

    result = runner.invoke(app, ["pods", "owns", "get"])

    assert result.exit_code == 0, result.output
    assert "owns nothing yet" in result.stdout


def test_owns_shows_a_problem_reading_the_measure(fake):
    fake.pods._standing = {
        "outcome": OUTCOME,
        "value": None,
        "problem": "The table conversations is not there.",
    }

    result = runner.invoke(app, ["pods", "owns"])

    assert result.exit_code == 0, result.output
    assert "cannot be read: The table conversations is not there." in result.stdout


def test_owns_set_sends_the_whole_outcome(fake, tmp_path):
    measure = tmp_path / "measure.sql"
    measure.write_text("SELECT count(*) FROM steps WHERE done\n")

    result = runner.invoke(
        app,
        [
            "--json",
            "pods",
            "owns",
            "set",
            "The launch ships by December 1",
            "--kind",
            "date",
            "--target",
            "22",
            "--unit",
            "steps",
            "--due",
            "2026-12-01",
            "--measure-file",
            str(measure),
            "--pod",
            POD_UUID,
        ],
    )

    assert result.exit_code == 0, result.output
    pod_id, sent = fake.pods.calls["set"]
    assert pod_id == POD_UUID
    assert sent == {
        "sentence": "The launch ships by December 1",
        "kind": "date",
        "target": 22.0,
        "unit": "steps",
        "due": "2026-12-01",
        "measure": "SELECT count(*) FROM steps WHERE done",
    }
    assert json.loads(result.stdout)["value"] == 87.5


@pytest.mark.parametrize(
    "extra",
    [
        ["--kind", "date"],
        ["--kind", "week", "--due", "2026-12-01"],
        ["--kind", "week", "--measure", "SELECT 1", "--measure-file", "x.sql"],
    ],
)
def test_owns_set_refuses_a_shape_the_api_would_refuse(fake, tmp_path, extra):
    (tmp_path / "x.sql").write_text("SELECT 1")
    extra = [str(tmp_path / "x.sql") if item == "x.sql" else item for item in extra]

    result = runner.invoke(app, ["pods", "owns", "set", "Something checkable", *extra])

    assert result.exit_code == 2, result.output
    assert "set" not in fake.pods.calls


def test_owns_clear_confirms_and_clears(fake):
    result = runner.invoke(app, ["pods", "owns", "clear", POD_UUID, "--yes"])

    assert result.exit_code == 0, result.output
    assert fake.pods.calls["clear"] == POD_UUID


def test_owns_clear_without_yes_refuses_off_a_terminal(fake):
    result = runner.invoke(app, ["pods", "owns", "clear", POD_UUID])

    assert result.exit_code == 1
    assert "clear" not in fake.pods.calls
