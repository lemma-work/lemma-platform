"""A bundle's pod connectors: what one carries, and what reading it checks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lemma_pod_bundle import (
    RESOURCE_DIRS,
    load_bundle_connectors,
    normalize_resource_dir_name,
    read_bundle_connector,
    referenced_connector_slugs,
)
from lemma_pod_bundle.normalize import _normalize_pod_connector_payload
from lemma_pod_bundle.portability import _extract_portable_variables


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_an_exported_connector_carries_nothing_of_its_organization():
    """The API response names the installation and the account a SHARED one
    runs as; neither means anything in another org, so neither is written."""
    response = {
        "id": "7a0c…",
        "pod_id": "p",
        "slug": "support-inbox",
        "auth_config_id": "a2b1c3d4-0000-0000-0000-000000000000",
        "installation_name": "gmail-acme",
        "connector_id": "gmail",
        "mode": "SHARED",
        "shared_pod_account_id": "b2b1c3d4-0000-0000-0000-000000000000",
        "shared_account": {"pod_account_id": "b2b1…", "email": "support@acme"},
        "required": False,
        "purpose": "The team mailbox the triage agent answers from.",
        "visibility": "POD",
        "status": "READY",
        "created_by_user_id": "u",
    }

    assert _normalize_pod_connector_payload(response) == {
        "connector": "gmail",
        "mode": "SHARED",
        "required": False,
        "purpose": "The team mailbox the triage agent answers from.",
    }


def test_a_restricted_connector_keeps_its_visibility():
    payload = _normalize_pod_connector_payload(
        {"connector_id": "slack", "mode": "PER_MEMBER", "visibility": "RESTRICTED"}
    )
    assert payload == {
        "connector": "slack",
        "mode": "PER_MEMBER",
        "required": False,
        "visibility": "RESTRICTED",
    }


def test_connectors_are_a_resource_dir_and_come_first():
    """Everything else may name a pod connector, so it is declared first."""
    assert RESOURCE_DIRS[0] == "connectors"
    assert normalize_resource_dir_name("pod-connectors") == "connectors"
    assert normalize_resource_dir_name("connector") == "connectors"


def test_reading_a_connector_normalizes_and_checks_it():
    connector = read_bundle_connector(
        "gmail", {"connector": "gmail", "mode": "per_member", "required": True}
    )
    assert connector.mode == "PER_MEMBER"
    assert connector.required is True
    assert connector.visibility == "POD"
    assert not connector.is_shared


@pytest.mark.parametrize(
    ("slug", "payload", "complaint"),
    [
        ("Gmail!", {"connector": "gmail", "mode": "SHARED"}, "slug"),
        ("gmail", {"mode": "SHARED"}, "`connector`"),
        ("gmail", {"connector": "gmail", "mode": "BOTH"}, "`mode`"),
        (
            "gmail",
            {"connector": "gmail", "mode": "SHARED", "required": "yes"},
            "required",
        ),
        (
            "gmail",
            {"connector": "gmail", "mode": "SHARED", "auth_config_id": "x"},
            "auth_config_id",
        ),
        (
            "gmail",
            {"connector": "gmail", "mode": "SHARED", "visibility": "PERSONAL"},
            "visibility",
        ),
    ],
)
def test_a_malformed_connector_is_refused_with_its_reason(slug, payload, complaint):
    with pytest.raises(ValueError, match=complaint):
        read_bundle_connector(slug, payload)


def test_load_bundle_connectors_reads_every_one_by_slug(tmp_path: Path):
    _write(
        tmp_path / "connectors" / "support-inbox" / "support-inbox.json",
        {"connector": "gmail", "mode": "SHARED"},
    )
    _write(
        tmp_path / "connectors" / "gmail" / "gmail.json",
        {"connector": "gmail", "mode": "PER_MEMBER", "required": True},
    )

    connectors = load_bundle_connectors(tmp_path)

    assert [(c.slug, c.connector, c.mode) for c in connectors] == [
        ("gmail", "gmail", "PER_MEMBER"),
        ("support-inbox", "gmail", "SHARED"),
    ]
    assert load_bundle_connectors(tmp_path / "nowhere") == []


def test_what_refers_to_a_connector_is_found_by_slug(tmp_path: Path):
    _write(
        tmp_path / "schedules" / "on_mail" / "on_mail.json",
        {"name": "on_mail", "pod_connector": "support-inbox"},
    )
    _write(
        tmp_path / "surfaces" / "slack" / "slack.json",
        {"platform": "SLACK", "pod_connector": "slack-bot"},
    )
    _write(
        tmp_path / "agents" / "triage" / "triage.json",
        {
            "name": "triage",
            "permissions": {
                "grants": [
                    {
                        "resource_type": "connector",
                        "resource_name": "support-inbox",
                        "permission_ids": ["connector.use"],
                    },
                    {
                        "resource_type": "table",
                        "resource_name": "tickets",
                        "permission_ids": ["table.read"],
                    },
                ]
            },
        },
    )

    assert referenced_connector_slugs(tmp_path) == {
        "support-inbox": ["schedules/on_mail", "agents/triage"],
        "slack-bot": ["surfaces/slack"],
    }


def test_an_export_that_wrote_an_installation_into_a_connector_fails(tmp_path: Path):
    _write(tmp_path / "pod.json", {"name": "demo"})
    _write(
        tmp_path / "connectors" / "gmail" / "gmail.json",
        {"connector": "gmail", "mode": "SHARED", "shared_pod_account_id": "x"},
    )

    with pytest.raises(ValueError, match="names an installation or an account"):
        _extract_portable_variables(tmp_path)


def test_a_clean_connector_exports_unchanged(tmp_path: Path):
    _write(tmp_path / "pod.json", {"name": "demo"})
    manifest = tmp_path / "connectors" / "gmail" / "gmail.json"
    _write(manifest, {"connector": "gmail", "mode": "PER_MEMBER", "required": True})

    assert _extract_portable_variables(tmp_path) == {}
    assert json.loads(manifest.read_text()) == {
        "connector": "gmail",
        "mode": "PER_MEMBER",
        "required": True,
    }
