"""The CLI's direct export and import of a bundle's pod connectors.

The client is a fake handed in, recording what the import asked the API to do;
the planning and declaring under test are the CLI's own.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from lemma_cli.cli_app.pod_bundle_connectors import (
    declare_pod_connectors,
    export_pod_connectors,
    plan_pod_connectors,
)


class _Connectors:
    def __init__(self, *, declared=(), installations=(), pod_accounts=(), refuse=()):
        self.declared = list(declared)
        self.installations = list(installations)
        self.in_pod = list(pod_accounts)
        #: Slugs the API refuses to declare.
        self.refuse = set(refuse)
        self.calls: list[tuple] = []
        self.auth_configs = SimpleNamespace(
            list=lambda limit=100: {"items": self.installations},
            create=self._create_install,
        )

    def _create_install(self, request):
        body = request.to_dict()
        self.calls.append(("install", body["connector_id"], body["config_source"]))
        made = {"id": "made-1", "name": body["connector_id"], "status": "ACTIVE"}
        self.installations.append({**made, "connector_id": body["connector_id"]})
        return made

    def pod_connectors(self):
        return {"items": self.declared}

    def pod_accounts(self):
        return {"items": self.in_pod}

    def add_pod_account(self, account_id, *, visibility="PERSONAL"):
        self.calls.append(("share", account_id, visibility))
        return {"id": f"pa-{account_id}"}

    def update_pod_account(self, pod_account_id, changes):
        self.calls.append(("widen", pod_account_id, changes))
        return {"id": pod_account_id}

    def remove_pod_account(self, pod_account_id):
        self.calls.append(("remove", pod_account_id))

    def declare_pod_connector(self, slug, auth_config_id, mode, **kwargs):
        self.calls.append(("declare", slug, auth_config_id, mode, kwargs))
        if slug in self.refuse:
            raise RuntimeError(f"refused: {slug}")
        return {"slug": slug}

    def update_pod_connector(self, slug, changes):
        self.calls.append(("update", slug, changes))
        return {"slug": slug}


class _Client:
    def __init__(self, connectors: _Connectors):
        self.connectors = connectors
        self.scoped_to: list[str | None] = []
        self.pods = SimpleNamespace(get=lambda pod_id: {"organization_id": "org-b"})

    def pod(self, pod_id, *, org_id=None):
        self.scoped_to.append(org_id)
        return SimpleNamespace(connectors=self.connectors)


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _bundle(root: Path) -> Path:
    _write(
        root / "connectors" / "support-inbox" / "support-inbox.json",
        {"connector": "gmail", "mode": "SHARED"},
    )
    _write(
        root / "connectors" / "crm" / "crm.json",
        {"connector": "hubspot", "mode": "PER_MEMBER", "required": True},
    )
    return root


_INSTALLS = [
    {"id": "gm-2", "name": "gmail-2", "connector_id": "gmail", "status": "ACTIVE"},
    {"id": "gm-1", "name": "gmail", "connector_id": "gmail", "is_default": True},
    {"id": "hs-1", "name": "hubspot", "connector_id": "hubspot", "is_default": True},
]


def test_export_writes_each_connector_without_its_organization(tmp_path: Path):
    pod_sdk = SimpleNamespace(
        connectors=_Connectors(
            declared=[
                {
                    "slug": "support-inbox",
                    "connector_id": "gmail",
                    "auth_config_id": "gm-1",
                    "mode": "SHARED",
                    "shared_pod_account_id": "pa-9",
                    "required": False,
                    "visibility": "POD",
                }
            ]
        )
    )

    written = export_pod_connectors(
        pod_sdk, tmp_path, should_export_name=lambda raw, slug: True
    )

    manifest = tmp_path / "connectors" / "support-inbox" / "support-inbox.json"
    assert json.loads(manifest.read_text()) == {
        "connector": "gmail",
        "mode": "SHARED",
        "required": False,
    }
    assert written == [json.loads(manifest.read_text())]


def test_import_maps_to_the_default_installation_and_shares_only_what_was_named(
    tmp_path: Path,
):
    connectors = _Connectors(installations=_INSTALLS)
    client = _Client(connectors)
    notices: list[str] = []

    pod_sdk, planned, kept = plan_pod_connectors(
        client,
        pod_id="pod-b",
        source_dir=_bundle(tmp_path),
        share_accounts={"support-inbox": "acct-mine"},
    )
    declare_pod_connectors(pod_sdk, planned, on_notice=notices.append)

    # The pod's own organization, not whichever one the CLI points at.
    assert client.scoped_to == ["org-b"]
    assert kept == []
    assert connectors.calls == [
        (
            "declare",
            "crm",
            "hs-1",
            "PER_MEMBER",
            {
                "shared_pod_account_id": None,
                "required": True,
                "purpose": None,
            },
        ),
        ("share", "acct-mine", "POD"),
        (
            "declare",
            "support-inbox",
            "gm-1",
            "SHARED",
            {
                "shared_pod_account_id": "pa-acct-mine",
                "required": False,
                "purpose": None,
            },
        ),
    ]
    assert any("members will be asked to connect" in n for n in notices)


def test_a_shared_connector_with_no_account_named_imports_needing_one(tmp_path: Path):
    connectors = _Connectors(installations=_INSTALLS)
    notices: list[str] = []
    pod_sdk, planned, _ = plan_pod_connectors(
        _Client(connectors),
        pod_id="pod-b",
        source_dir=_bundle(tmp_path),
        installations={"support-inbox": "gmail-2"},
    )
    declare_pod_connectors(pod_sdk, planned, on_notice=notices.append)

    declared = {call[1]: call for call in connectors.calls if call[0] == "declare"}
    assert declared["support-inbox"][2] == "gm-2"
    assert declared["support-inbox"][4]["shared_pod_account_id"] is None
    assert not [call for call in connectors.calls if call[0] == "share"]
    assert any("needing an account" in n for n in notices)


def test_a_connector_with_no_installation_stops_before_anything_is_written(
    tmp_path: Path,
):
    connectors = _Connectors(installations=[_INSTALLS[1]])

    with pytest.raises(
        ValueError, match="no hubspot installation.*--create-installations"
    ):
        plan_pod_connectors(
            _Client(connectors), pod_id="pod-b", source_dir=_bundle(tmp_path)
        )
    assert connectors.calls == []

    pod_sdk, planned, _ = plan_pod_connectors(
        _Client(connectors),
        pod_id="pod-b",
        source_dir=_bundle(tmp_path),
        create_installations=True,
    )
    declare_pod_connectors(pod_sdk, planned, on_notice=lambda _n: None)
    assert ("install", "hubspot", "SYSTEM_DEFAULT") in connectors.calls
    crm = next(c for c in connectors.calls if c[:2] == ("declare", "crm"))
    assert crm[2] == "made-1"


def test_a_slug_the_pod_has_is_kept_and_a_clashing_one_refused(tmp_path: Path):
    kept_pod = _Connectors(
        installations=_INSTALLS,
        declared=[{"slug": "crm", "connector_id": "hubspot", "mode": "PER_MEMBER"}],
    )
    _, planned, kept = plan_pod_connectors(
        _Client(kept_pod), pod_id="pod-b", source_dir=_bundle(tmp_path)
    )
    assert kept == ["crm"]
    assert [item.connector.slug for item in planned] == ["support-inbox"]

    clashing = _Connectors(
        installations=_INSTALLS,
        declared=[{"slug": "crm", "connector_id": "salesforce", "mode": "PER_MEMBER"}],
    )
    with pytest.raises(ValueError, match="already has one for salesforce"):
        plan_pod_connectors(
            _Client(clashing), pod_id="pod-b", source_dir=_bundle(tmp_path)
        )


def test_a_choice_for_a_slug_the_bundle_lacks_is_refused(tmp_path: Path):
    with pytest.raises(ValueError, match="No connector named typo"):
        plan_pod_connectors(
            _Client(_Connectors(installations=_INSTALLS)),
            pod_id="pod-b",
            source_dir=_bundle(tmp_path),
            share_accounts={"typo": "acct"},
        )


def test_a_per_member_connector_cannot_share_an_account(tmp_path: Path):
    with pytest.raises(ValueError, match="per member"):
        plan_pod_connectors(
            _Client(_Connectors(installations=_INSTALLS)),
            pod_id="pod-b",
            source_dir=_bundle(tmp_path),
            share_accounts={"crm": "acct"},
        )


def test_a_slug_the_pod_has_in_the_other_mode_is_refused(tmp_path: Path):
    """Same catalog connector, other mode: keeping the pod's would run the
    bundle's resources as an account they were not built for."""
    other_mode = _Connectors(
        installations=_INSTALLS,
        declared=[{"slug": "crm", "connector_id": "hubspot", "mode": "SHARED"}],
    )
    with pytest.raises(ValueError, match="crm.*shared.*per member"):
        plan_pod_connectors(
            _Client(other_mode), pod_id="pod-b", source_dir=_bundle(tmp_path)
        )
    assert other_mode.calls == []


def test_an_account_shared_for_a_refused_connector_is_taken_back(tmp_path: Path):
    """Sharing and declaring are two calls from the CLI: when the declaration
    is refused, the account does not stay shared with the pod for nothing."""
    connectors = _Connectors(installations=_INSTALLS, refuse={"support-inbox"})
    pod_sdk, planned, _ = plan_pod_connectors(
        _Client(connectors),
        pod_id="pod-b",
        source_dir=_bundle(tmp_path),
        share_accounts={"support-inbox": "acct-mine"},
    )

    with pytest.raises(RuntimeError, match="refused"):
        declare_pod_connectors(pod_sdk, planned, on_notice=lambda _n: None)

    assert connectors.calls[-2][:2] == ("declare", "support-inbox")
    assert connectors.calls[-1] == ("remove", "pa-acct-mine")


def test_an_account_already_in_the_pod_is_widened_and_put_back(tmp_path: Path):
    connectors = _Connectors(
        installations=_INSTALLS,
        refuse={"support-inbox"},
        pod_accounts=[
            {"id": "pa-7", "account_id": "acct-mine", "visibility": "PERSONAL"}
        ],
    )
    pod_sdk, planned, _ = plan_pod_connectors(
        _Client(connectors),
        pod_id="pod-b",
        source_dir=_bundle(tmp_path),
        share_accounts={"support-inbox": "acct-mine"},
    )

    with pytest.raises(RuntimeError):
        declare_pod_connectors(pod_sdk, planned, on_notice=lambda _n: None)

    widened = [call for call in connectors.calls if call[0] == "widen"]
    assert widened == [
        ("widen", "pa-7", {"visibility": "POD"}),
        ("widen", "pa-7", {"visibility": "PERSONAL"}),
    ]
    assert not [call for call in connectors.calls if call[0] == "share"]


def test_an_account_shared_with_only_some_people_is_refused_before_anything(
    tmp_path: Path,
):
    connectors = _Connectors(
        installations=_INSTALLS,
        pod_accounts=[
            {"id": "pa-7", "account_id": "acct-mine", "visibility": "RESTRICTED"}
        ],
    )
    with pytest.raises(ValueError, match="shared with some people"):
        plan_pod_connectors(
            _Client(connectors),
            pod_id="pod-b",
            source_dir=_bundle(tmp_path),
            share_accounts={"support-inbox": "acct-mine"},
        )
    assert connectors.calls == []
