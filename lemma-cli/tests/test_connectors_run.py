"""`lemma connectors run` and the resolution it shares with `operations`.

Executing anything through a connector used to take four commands: `overview` to
learn the org-local auth-config name, `operations search` for the operation id,
`operations get` for the payload shape, then `execute`. Every one of those is a
separate agent step. These tests pin the collapsed flow — and the resolver rules
that make a bare connector id enough.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from typer.testing import CliRunner

from lemma_cli.cli_core.app import app
from lemma_cli.cli_core.commands import connectors
from lemma_sdk.errors import LemmaNotFoundError

runner = CliRunner()


_INSTALLS = [
    {
        "id": "ac-1",
        "name": "workspace-gmail",
        "connector_id": "gmail",
        "kind": "LEMMA",
        "is_default": True,
    },
    {
        "id": "ac-2",
        "name": "legacy-gmail",
        "connector_id": "gmail",
        "kind": "COMPOSIO",
    },
    {"id": "ac-3", "name": "team-slack", "connector_id": "slack", "kind": "LEMMA"},
]

_OPERATIONS = {
    # Ranked first for "list recent emails" on the live catalog — a lexical match
    # on "email" that mutates. This ordering is the bug, reproduced.
    "gmail_add_label_to_email": {
        "name": "gmail_add_label_to_email",
        "description": "Add a label to an email in the list of recent emails.",
        "input_schema": {
            "type": "object",
            "properties": {"message_id": {"type": "string"}},
            "required": ["message_id"],
        },
    },
    "gmail_list_messages": {
        "name": "gmail_list_messages",
        "description": "List recent emails in the mailbox.",
        "input_schema": {
            "type": "object",
            "properties": {"max_results": {"type": "integer"}},
        },
    },
    "gmail_send_email": {
        "name": "gmail_send_email",
        "description": "Send an email.",
        "input_schema": {
            "type": "object",
            "properties": {"to": {"type": "string"}},
            "required": ["to"],
        },
    },
}


def _fake_client(captured: dict):
    class Operations:
        def search(self, auth_config, query=None, *, limit=100):
            captured.setdefault("searches", []).append((auth_config, query, limit))
            hits = [
                {"name": name, "description": op["description"]}
                for name, op in _OPERATIONS.items()
                if not query or query.split()[0].lower() in op["description"].lower()
            ] or [
                {"name": name, "description": op["description"]}
                for name, op in _OPERATIONS.items()
            ]
            return {"connector_id": "gmail", "items": hits, "returned_count": len(hits)}

        def batch(self, auth_config, operations):
            captured.setdefault("batches", []).append((auth_config, list(operations)))
            items = [_OPERATIONS[name] for name in operations if name in _OPERATIONS]
            if operations and not items:
                raise KeyError(operations[0])
            return {
                "connector_id": "gmail",
                "items": items,
                "returned_count": len(items),
            }

        def get(self, auth_config, operation):
            return _OPERATIONS[operation]

        def execute(
            self, auth_config, operation, payload, *, account_id=None, pod_id=None
        ):
            # The deprecated org-level route (`--account`, `--auth-config`).
            captured["deprecated_execute"] = {
                "auth_config": auth_config,
                "operation": operation,
                "payload": payload,
                "account_id": account_id,
                "pod_id": pod_id,
            }
            return {"result": {"ok": True}}

        def search_all(self, query=None, *, limit=100):
            # The org-wide endpoint: ONE request, hits labelled with the install
            # to execute them against.
            captured["search_all"] = (query, limit)
            hits = [
                {
                    "name": name,
                    "description": op["description"],
                    "auth_config": "workspace-gmail",
                    "connector_id": "gmail",
                }
                for name, op in _OPERATIONS.items()
                if not query or query.split()[0].lower() in op["description"].lower()
            ]
            return {"items": hits[:limit], "returned_count": len(hits[:limit])}

    class Accounts:
        def list(self, *, app=None, limit=100):
            return {"items": [{"id": "acct-9", "email": "anukul@lemma.work"}]}

    class AuthConfigs:
        def list(self, *, limit=100):
            captured["auth_config_lists"] = captured.get("auth_config_lists", 0) + 1
            return {"items": list(_INSTALLS)}

    class Connectors:
        def __init__(self):
            self.operations = Operations()
            self.accounts = Accounts()
            self.auth_configs = AuthConfigs()

        def connect_request(self, connector, *, auth_config_id=None, **extra):
            captured["connect_request"] = {"connector": connector, **extra}
            return {"authorization_url": "https://acme.myshopify.com/admin/oauth"}

    class PodConnectors:
        """A pod's connectors: what `connectors run`/`execute` now talk to."""

        def __init__(self, pod_id):
            self.pod_id = pod_id

        def pod_connectors(self):
            captured.setdefault("listed_pods", []).append(self.pod_id)
            return {
                "items": [
                    {"slug": slug, "mode": "PER_MEMBER", "status": "READY", "id": "x"}
                    for slug in _POD_CONNECTORS
                ]
            }

        def pod_operation(self, connector, operation):
            captured.setdefault("op_gets", []).append((connector, operation))
            if connector not in _POD_CONNECTORS:
                raise LemmaNotFoundError(
                    404, "no such connector", code="POD_CONNECTOR_NOT_FOUND"
                )
            if operation not in _OPERATIONS:
                raise LemmaNotFoundError(404, "no such operation")
            return _OPERATIONS[operation]

        def pod_operations(self, connector, query=None, *, limit=100):
            captured.setdefault("pod_searches", []).append((connector, query, limit))
            hits = [
                {"name": name, "description": op["description"]}
                for name, op in _OPERATIONS.items()
                if not query or query.split()[0].lower() in op["description"].lower()
            ] or [
                {"name": name, "description": op["description"]}
                for name, op in _OPERATIONS.items()
            ]
            return {"items": hits[:limit]}

        def declare_pod_connector(self, slug, auth_config_id, mode, **extra):
            captured["declare"] = {
                "slug": slug,
                "auth_config_id": auth_config_id,
                "mode": mode,
                **extra,
            }
            return {"slug": slug}

        def add_pod_account(self, account_id, *, visibility="PERSONAL"):
            captured["add_account"] = (account_id, visibility)
            return {"id": "pa-1"}

        def share_pod_account_as(self, pod_account_id, slug, *, purpose=None):
            captured["share_as"] = (pod_account_id, slug, purpose)
            return {"slug": slug}

        def connector_coverage(self, slug):
            captured["coverage"] = slug
            return {"eligible": 2, "connected": 1, "waiting_on": [], "truncated": False}

        def ask_members_to_connect(self, slug, *, user_ids=None):
            captured["ask"] = (slug, user_ids)
            return {
                "asked": [],
                "asked_recently": [],
                "not_asked": [],
                "connect_url": "x",
            }

        def execute(self, connector, operation, payload):
            if connector not in _POD_CONNECTORS:
                raise LemmaNotFoundError(
                    404, "no such connector", code="POD_CONNECTOR_NOT_FOUND"
                )
            captured["execute"] = {
                "pod_id": self.pod_id,
                "connector": connector,
                "operation": operation,
                "payload": payload,
            }
            return {"result": {"ok": True}}

    return SimpleNamespace(
        connectors=Connectors(),
        pod=lambda pod_id: SimpleNamespace(connectors=PodConnectors(pod_id)),
    )


_POD = "11111111-1111-1111-1111-111111111111"
_POD_CONNECTORS = ("gmail", "support-inbox")


def _patch(monkeypatch, client, *, pod=_POD):
    runtime = {"org": "org-1", **({"pod": pod} if pod else {})}
    monkeypatch.setattr(
        connectors,
        "run_with_client",
        lambda ctx, fn: fn(client, SimpleNamespace(config={"_runtime": runtime})),
    )


def test_run_resolves_connector_id_operation_and_executes(monkeypatch):
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))

    result = runner.invoke(
        app,
        [
            "connectors",
            "run",
            "gmail",
            "gmail_list_messages",
            "-d",
            json.dumps({"max_results": 5}),
        ],
    )

    assert result.exit_code == 0, result.stdout
    # `gmail` is the pod connector's slug, run in the selected pod: the
    # connector, not the caller, decides which account it runs as.
    assert captured["execute"] == {
        "pod_id": _POD,
        "connector": "gmail",
        "operation": "gmail_list_messages",
        "payload": {"max_results": 5},
    }
    # Discovery went through the same pod connector.
    assert captured["op_gets"] == [("gmail", "gmail_list_messages")]


def test_run_resolves_a_plain_english_intent(monkeypatch):
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))

    result = runner.invoke(
        app, ["connectors", "run", "gmail", "List recent emails", "--dry-run"]
    )

    assert result.exit_code == 0, result.stdout
    # A dry run resolves and reports the schema without calling execute.
    assert "execute" not in captured
    assert "gmail_list_messages" in result.stdout


def test_run_prints_the_schema_instead_of_failing_when_input_is_missing(monkeypatch):
    """An operation with required input and no --data is the moment you need the
    schema, not an error."""
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))

    result = runner.invoke(app, ["connectors", "run", "gmail", "gmail_send_email"])

    assert result.exit_code == 0, result.stdout
    assert "execute" not in captured
    assert "to" in result.stdout  # the required field is named


def test_run_reports_an_unknown_connector_with_what_the_pod_declares(monkeypatch):
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))

    result = runner.invoke(app, ["connectors", "run", "notion", "anything"])

    assert result.exit_code != 0
    # The error names the pod's connectors, so the next command is obvious.
    assert "support-inbox" in result.output


def test_run_needs_a_pod_for_a_name_that_is_no_installation(monkeypatch):
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured), pod=None)
    monkeypatch.delenv("LEMMA_POD_ID", raising=False)

    result = runner.invoke(
        app, ["connectors", "run", "notion", "gmail_list_messages", "-d", "{}"]
    )

    assert result.exit_code != 0
    assert "execute" not in captured
    assert "deprecated_execute" not in captured


def test_search_with_no_connector_searches_every_install(monkeypatch):
    """`operations search "send email"` must answer without being told which
    connector to look in.

    It used to bind the query to the auth-config slot and 404. Classifying the
    positional correctly wasn't enough either — auto-discovery then refused
    whenever more than one connector was installed, which is every real org, so
    the documented one-positional form still hard-errored.
    """
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))

    result = runner.invoke(
        app,
        ["--json", "connectors", "operations", "search", "send email", "--limit", "5"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    names = [item["name"] for item in payload["items"]]
    assert "gmail_send_email" in names
    # Every hit says which install to pass on, so the result is directly usable.
    assert all(item["auth_config"] for item in payload["items"])
    # ONE request to the org-wide endpoint — no client-side fan-out. This used to
    # issue one call per install, ten on a real org.
    assert captured["search_all"] == ("send email", 5)
    assert "searches" not in captured


def test_search_with_a_named_connector_stays_one_request(monkeypatch):
    """Fanning out is the fallback, not the default — naming a connector must not
    start paying per-install."""
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))

    result = runner.invoke(
        app, ["connectors", "operations", "search", "gmail", "send email"]
    )

    assert result.exit_code == 0, result.output
    assert {call[0] for call in captured["searches"]} == {"workspace-gmail"}


def test_search_folds_input_schemas_into_short_result_lists(monkeypatch):
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))

    result = runner.invoke(
        app,
        [
            "--json",
            "connectors",
            "operations",
            "search",
            "-c",
            "gmail",
            "-q",
            "send",
            "--limit",
            "3",
        ],
    )

    assert result.exit_code == 0, result.output
    # The schemas came from ONE extra batch call, not a get-per-hit.
    assert len(captured.get("batches", [])) == 1
    payload = json.loads(result.stdout)
    assert payload["items"][0]["input_schema"]["properties"] == {
        "to": {"type": "string"}
    }


def test_operations_get_auto_discovers_like_its_siblings(monkeypatch):
    """`get` was the one operations command that demanded an explicit auth
    config, so a single-install org still had to name it."""
    captured: dict = {}
    client = _fake_client(captured)
    monkeypatch.setattr(
        connectors, "_auth_config_items", lambda _client: [_INSTALLS[2]]
    )
    _patch(monkeypatch, client)

    result = runner.invoke(app, ["connectors", "operations", "get", "gmail_send_email"])

    assert result.exit_code == 0, result.stdout
    assert "gmail_send_email" in result.stdout


def test_execute_runs_the_named_pod_connector_without_resolving_installs(monkeypatch):
    """A pod connector slug is the whole address: nothing to look up first."""
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))

    result = runner.invoke(
        app,
        ["connectors", "operations", "execute", "support-inbox", "gmail_send_email"],
    )

    assert result.exit_code == 0, result.stdout
    assert "auth_config_lists" not in captured
    assert captured["execute"]["connector"] == "support-inbox"
    assert captured["execute"]["pod_id"] == _POD


def test_read_intent_skips_a_higher_ranked_write_operation(monkeypatch):
    """Ranked search is lexical, so "list recent emails" matched ADD_LABEL_TO_EMAIL
    above FETCH_EMAILS on the live catalog. A read intent must prefer the best
    NON-mutating hit — silently editing a mailbox someone asked to read is the
    worst failure this command can have."""
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))

    result = runner.invoke(
        app, ["connectors", "run", "gmail", "list recent emails", "--dry-run"]
    )

    assert result.exit_code == 0, result.output
    assert "gmail_list_messages" in result.output
    assert "-> gmail_add_label_to_email" not in result.output


def test_inferred_write_operation_refuses_to_run_without_confirmation(monkeypatch):
    """An operation nobody named, that changes data, must not just run."""
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))

    result = runner.invoke(
        app,
        [
            "connectors",
            "run",
            "gmail",
            "label that message",
            "-d",
            json.dumps({"message_id": "m1"}),
        ],
    )

    assert result.exit_code != 0
    assert "execute" not in captured
    assert "CHANGES data" in result.output


def test_inferred_write_runs_with_explicit_confirmation(monkeypatch):
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))

    result = runner.invoke(
        app,
        [
            "connectors",
            "run",
            "gmail",
            "label that message",
            "-d",
            json.dumps({"message_id": "m1"}),
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["execute"]["operation"] == "gmail_add_label_to_email"


def test_naming_a_write_operation_explicitly_is_not_gated(monkeypatch):
    """The gate is about INFERENCE, not about writes. Naming the op is consent."""
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))

    result = runner.invoke(
        app,
        [
            "connectors",
            "run",
            "gmail",
            "gmail_add_label_to_email",
            "-d",
            json.dumps({"message_id": "m1"}),
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["execute"]["operation"] == "gmail_add_label_to_email"


def test_attach_turns_pod_paths_into_file_references(monkeypatch):
    """A connector file argument is a reference the server reads as the
    caller. Repeating a field makes a list, and a dotted field nests."""
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))
    monkeypatch.setenv("LEMMA_POD_ID", "11111111-1111-1111-1111-111111111111")

    result = runner.invoke(
        app,
        [
            "connectors",
            "operations",
            "execute",
            "gmail",
            "gmail_send_email",
            "-d",
            '{"to": "anukul@lemma.work"}',
            "--attach",
            "attachments=/me/a.pdf",
            "--attach",
            "attachments=/me/b.pdf",
            "--attach",
            "body.file=/shared/c.csv",
        ],
    )

    assert result.exit_code == 0, result.output
    sent = captured["execute"]
    assert sent["payload"] == {
        "to": "anukul@lemma.work",
        "attachments": [{"pod_path": "/me/a.pdf"}, {"pod_path": "/me/b.pdf"}],
        "body": {"file": {"pod_path": "/shared/c.csv"}},
    }
    # The paths resolve in the pod the connector is run in.
    assert sent["pod_id"] == _POD


def test_connect_request_passes_connection_fields(monkeypatch):
    """Shopify cannot start a sign-in without the store name."""
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))

    result = runner.invoke(
        app,
        [
            "connectors",
            "connect-requests",
            "create",
            "shopify",
            "--field",
            "subdomain=acme",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["connect_request"] == {
        "connector": "shopify",
        "connection_fields": {"subdomain": "acme"},
    }

    refused = runner.invoke(
        app, ["connectors", "connect-requests", "create", "shopify", "--field", "acme"]
    )
    assert refused.exit_code != 0


def test_pod_list_and_pod_operations_read_the_selected_pods_connectors(monkeypatch):
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))

    listed = runner.invoke(app, ["--json", "connectors", "pod-list"])
    assert listed.exit_code == 0, listed.output
    assert [i["slug"] for i in json.loads(listed.stdout)["items"]] == list(
        _POD_CONNECTORS
    )

    found = runner.invoke(
        app, ["--json", "connectors", "pod-operations", "gmail", "send", "--limit", "3"]
    )
    assert found.exit_code == 0, found.output
    assert captured["pod_searches"] == [("gmail", "send", 3)]


def test_pod_list_shows_slugs_and_takes_another_pod(monkeypatch):
    """Its help promised slug, mode and status; the generic table showed
    status and id. And it was the one pod command without `--pod`."""
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))
    other = "22222222-2222-2222-2222-222222222222"

    listed = runner.invoke(app, ["connectors", "pod-list", "--pod", other])

    assert listed.exit_code == 0, listed.output
    for slug in _POD_CONNECTORS:
        assert slug in listed.stdout
    assert "PER_MEMBER" in listed.stdout
    assert captured["listed_pods"] == [other]


def test_pod_add_declares_an_installation_under_a_slug(monkeypatch):
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))

    result = runner.invoke(
        app,
        [
            "connectors",
            "pod-add",
            "support-inbox",
            "gmail",
            "--mode",
            "SHARED",
            "--shared-account",
            "pa-1",
            "--purpose",
            "The team inbox",
        ],
    )

    assert result.exit_code == 0, result.output
    # `gmail` resolved to the org's default Gmail install, by id.
    assert captured["declare"] == {
        "slug": "support-inbox",
        "auth_config_id": "ac-1",
        "mode": "SHARED",
        "shared_pod_account_id": "pa-1",
        "required": False,
        "purpose": "The team inbox",
    }


def test_pod_share_brings_ones_own_account_into_the_pod_shared(monkeypatch):
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))

    result = runner.invoke(app, ["connectors", "pod-share", "acct-9"])

    assert result.exit_code == 0, result.output
    assert captured["add_account"] == ("acct-9", "POD")


def test_pod_share_as_shares_and_points_in_one_call(monkeypatch):
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))

    result = runner.invoke(
        app,
        ["connectors", "pod-share-as", "pa-1", "support-inbox", "--purpose", "Inbox"],
    )

    assert result.exit_code == 0, result.output
    assert captured["share_as"] == ("pa-1", "support-inbox", "Inbox")


def test_pod_coverage_and_pod_ask_reach_the_selected_pods_connector(monkeypatch):
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))

    covered = runner.invoke(app, ["--json", "connectors", "pod-coverage", "gmail"])
    assert covered.exit_code == 0, covered.output
    assert json.loads(covered.stdout)["connected"] == 1
    assert captured["coverage"] == "gmail"

    everyone = runner.invoke(app, ["connectors", "pod-ask", "gmail"])
    assert everyone.exit_code == 0, everyone.output
    assert captured["ask"] == ("gmail", None)

    some = runner.invoke(
        app, ["connectors", "pod-ask", "gmail", "--user", "u1", "--user", "u2"]
    )
    assert some.exit_code == 0, some.output
    assert captured["ask"] == ("gmail", ["u1", "u2"])


# --- Deprecated: addressing an installation and an account -----------------
#
# Live scripts, agents and pod functions still pass `--account`/`--auth-config`
# or an `account_id` in the data. They keep working, through the deprecated
# org-level route the server still serves, hidden from help and warned about.


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_run_with_an_account_takes_the_deprecated_route_and_warns(monkeypatch):
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))

    result = runner.invoke(
        app,
        [
            "connectors",
            "run",
            "gmail",
            "gmail_list_messages",
            "-d",
            "{}",
            "--account",
            "anukul@lemma.work",
        ],
    )

    assert result.exit_code == 0, result.output
    # The email resolved to its account; `gmail` to the default installation.
    assert captured["deprecated_execute"] == {
        "auth_config": "workspace-gmail",
        "operation": "gmail_list_messages",
        "payload": {},
        "account_id": "acct-9",
        "pod_id": None,
    }
    assert "execute" not in captured  # never the pod connector route
    assert "op_gets" not in captured  # resolved on the installation instead
    assert "warning: --account is deprecated" in _flat(result.stderr)


def test_execute_with_auth_config_and_account_takes_the_deprecated_route(
    monkeypatch,
):
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))

    result = runner.invoke(
        app,
        [
            "connectors",
            "operations",
            "execute",
            "-c",
            "workspace-gmail",
            "gmail_send_email",
            "--account-id",
            "acct-1",
            "-d",
            '{"to": "a@example.com"}',
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["deprecated_execute"] == {
        "auth_config": "workspace-gmail",
        "operation": "gmail_send_email",
        "payload": {"to": "a@example.com"},
        "account_id": "acct-1",
        "pod_id": None,
    }
    assert "execute" not in captured
    assert "warning: --auth-config and --account is deprecated" in _flat(result.stderr)


def test_execute_with_account_id_in_the_data_takes_the_deprecated_route(
    monkeypatch,
):
    """`account_id` in --data was refused for a while; it is accepted again,
    with a warning, and CONNECTOR read as an installation (or connector id)."""
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))

    result = runner.invoke(
        app,
        [
            "connectors",
            "operations",
            "execute",
            "gmail",
            "gmail_send_email",
            "-d",
            json.dumps({"payload": {"to": "a@example.com"}, "account_id": "acct-1"}),
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["deprecated_execute"]["auth_config"] == "workspace-gmail"
    assert captured["deprecated_execute"]["account_id"] == "acct-1"
    assert captured["deprecated_execute"]["payload"] == {"to": "a@example.com"}
    assert "execute" not in captured
    assert "account_id in --data is deprecated" in _flat(result.stderr)


def test_a_deprecated_call_naming_a_pod_file_sends_the_pod(monkeypatch):
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))

    result = runner.invoke(
        app,
        [
            "connectors",
            "operations",
            "execute",
            "gmail",
            "gmail_send_email",
            "--account",
            "acct-1",
            "-d",
            '{"to": "a@example.com"}',
            "--attach",
            "attachments=/me/a.pdf",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["deprecated_execute"]["pod_id"] == _POD


def test_the_pod_connector_route_prints_no_deprecation(monkeypatch):
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))

    result = runner.invoke(
        app, ["connectors", "operations", "execute", "gmail", "gmail_list_messages"]
    )

    assert result.exit_code == 0, result.output
    assert "deprecated" not in result.stderr
    assert "deprecated_execute" not in captured


def test_deprecated_connector_flags_are_hidden_from_help():
    for command in (
        ["connectors", "run", "--help"],
        ["connectors", "operations", "execute", "--help"],
    ):
        result = runner.invoke(app, command)
        assert result.exit_code == 0, result.output
        assert "--account" not in result.output
        assert "--auth-config" not in result.output


# --- Deprecated: an installation name where a pod connector slug goes ------
#
# Before pod connectors, `run INSTALL OPERATION` ran on the caller's own
# account. It still does, through the deprecated org-level route, whenever no
# pod connector can take the call: no pod selected, or no slug by that name.


def test_run_with_no_pod_runs_an_installation_on_ones_own_account(monkeypatch):
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured), pod=None)
    monkeypatch.delenv("LEMMA_POD_ID", raising=False)

    result = runner.invoke(
        app, ["connectors", "run", "gmail", "gmail_list_messages", "-d", "{}"]
    )

    assert result.exit_code == 0, result.output
    assert captured["deprecated_execute"] == {
        "auth_config": "workspace-gmail",
        "operation": "gmail_list_messages",
        "payload": {},
        "account_id": None,  # the server uses the caller's own account
        "pod_id": None,
    }
    assert "execute" not in captured
    assert "op_gets" not in captured  # resolved on the installation
    assert "rather than a pod connector is deprecated" in _flat(result.stderr)


def test_run_falls_back_to_an_installation_the_pod_has_no_slug_for(monkeypatch):
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))

    result = runner.invoke(
        app, ["connectors", "run", "team-slack", "gmail_list_messages", "-d", "{}"]
    )

    assert result.exit_code == 0, result.output
    # The pod was asked first, and said it has no such connector.
    assert captured["op_gets"] == [("team-slack", "gmail_list_messages")]
    assert captured["deprecated_execute"]["auth_config"] == "team-slack"
    assert captured["deprecated_execute"]["account_id"] is None
    assert "execute" not in captured
    assert "rather than a pod connector is deprecated" in _flat(result.stderr)


def test_run_prefers_the_pod_slug_over_an_installation_of_that_name(monkeypatch):
    """`gmail` is both a slug and an installed connector id: the slug wins, and
    the happy path costs no installs lookup."""
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))

    result = runner.invoke(
        app, ["connectors", "run", "gmail", "gmail_list_messages", "-d", "{}"]
    )

    assert result.exit_code == 0, result.output
    assert captured["execute"]["connector"] == "gmail"
    assert captured["execute"]["pod_id"] == _POD
    assert "deprecated_execute" not in captured
    assert "auth_config_lists" not in captured
    assert "deprecated" not in result.stderr


def test_execute_with_no_pod_runs_an_installation_on_ones_own_account(monkeypatch):
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured), pod=None)
    monkeypatch.delenv("LEMMA_POD_ID", raising=False)

    result = runner.invoke(
        app,
        [
            "connectors",
            "operations",
            "execute",
            "workspace-gmail",
            "gmail_list_messages",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["deprecated_execute"]["auth_config"] == "workspace-gmail"
    assert captured["deprecated_execute"]["account_id"] is None
    assert "execute" not in captured
    assert "rather than a pod connector is deprecated" in _flat(result.stderr)


def test_execute_falls_back_to_an_installation_the_pod_has_no_slug_for(monkeypatch):
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))

    result = runner.invoke(
        app,
        ["connectors", "operations", "execute", "team-slack", "gmail_list_messages"],
    )

    assert result.exit_code == 0, result.output
    assert captured["deprecated_execute"]["auth_config"] == "team-slack"
    assert captured["deprecated_execute"]["account_id"] is None
    assert "execute" not in captured


def test_execute_still_reports_a_name_that_is_neither_slug_nor_install(monkeypatch):
    captured: dict = {}
    _patch(monkeypatch, _fake_client(captured))

    result = runner.invoke(
        app, ["connectors", "operations", "execute", "notion", "anything"]
    )

    assert result.exit_code != 0
    assert "deprecated_execute" not in captured
    assert "execute" not in captured
