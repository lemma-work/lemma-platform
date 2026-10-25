"""A bundle's pod connectors, for the CLI's direct export and import.

Export writes each pod connector as ``connectors/<slug>/<slug>.json``: the
catalog connector and the mode, never the installation or the account a
SHARED one runs as (``lemma_pod_bundle.normalize._normalize_pod_connector_
payload``, the backend exporter's own normalizer).

Import declares them first, before anything that names their slug. Which of
the importing org's installations each maps to, and which of the importer's
own accounts a SHARED one shares, are the importer's choices:

- ``--connector SLUG=INSTALLATION`` (name or id) picks an installation; left
  out, the org's default installation of the catalog connector is used;
- ``--create-installations`` installs Lemma's own app for a connector the org
  has no installation of (needs ``org.installation.manage``);
- ``--share-account SLUG=ACCOUNT_ID`` shares one of your own accounts for a
  SHARED connector. Left out, it is declared needing an account -- a warning,
  not a failure.

Every choice is checked before anything is written, so an import that cannot
declare its connectors stops with the pod untouched -- the backend's apply
refuses the same cases the same way: another catalog connector or the other
mode under a slug the pod already has is a conflict, not kept. Sharing an
account and declaring the connector on it are two calls here (the backend's
apply makes them one transaction), so an account widened for a connector that
is then refused is put back as it was.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lemma_pod_bundle import BundleConnector, load_bundle_connectors
from lemma_pod_bundle.layout import _write_json
from lemma_pod_bundle.normalize import _normalize_pod_connector_payload

from ..cli_core.io import list_items, to_plain


def export_pod_connectors(
    pod_sdk: Any,
    bundle_root: Path,
    *,
    should_export_name: Callable[[dict[str, Any], str], bool],
) -> list[dict[str, Any]]:
    """Write the pod's connectors into ``connectors/``; returns what was written."""
    written: list[dict[str, Any]] = []
    for item in list_items(pod_sdk.connectors.pod_connectors()):
        raw = to_plain(item)
        slug = str(raw.get("slug") or "")
        if not slug or not should_export_name(raw, slug):
            continue
        payload = _normalize_pod_connector_payload(raw)
        resource_dir = bundle_root / "connectors" / slug
        resource_dir.mkdir(parents=True, exist_ok=True)
        _write_json(resource_dir / f"{slug}.json", payload)
        written.append(payload)
    return written


@dataclass(frozen=True)
class _Planned:
    connector: BundleConnector
    #: An installation id, or None to install Lemma's app for it.
    auth_config_id: str | None
    share_account_id: str | None


_MODE_WORDS = {
    "PER_MEMBER": "per member (each person's own account)",
    "SHARED": "shared (one account for everyone)",
}


def _declared_conflict(
    connector: BundleConnector, existing_connector: str, existing_mode: str
) -> str | None:
    """Why the pod's own connector of this slug cannot stand for the bundle's."""
    if existing_connector != connector.connector:
        return (
            f"connector '{connector.slug}': this pod already has one for "
            f"{existing_connector}, not {connector.connector}. Rename or remove it."
        )
    if existing_mode != connector.mode:
        return (
            f"connector '{connector.slug}': this pod already has one for "
            f"{connector.connector} that is "
            f"{_MODE_WORDS.get(existing_mode, existing_mode)}, and this bundle's "
            f"is {_MODE_WORDS.get(connector.mode, connector.mode)}. Change or "
            "rename the pod's."
        )
    return None


def _pod_accounts_by_account(pod_sdk: Any) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("account_id")): item
        for item in list_items(pod_sdk.connectors.pod_accounts())
    }


def _check_share(
    connector: BundleConnector,
    account_id: str,
    *,
    in_pod: Mapping[str, dict[str, Any]],
) -> None:
    """Refuse, before anything is written, a share that could not be undone.

    An account the importer already shares with only some people would have to
    be widened to the whole pod, and putting its list back is not something
    this import can promise.
    """
    existing = in_pod.get(account_id)
    if existing and str(existing.get("visibility") or "").upper() == "RESTRICTED":
        raise ValueError(
            f"connector '{connector.slug}': account {account_id} is shared with "
            "some people in this pod. Share it with the whole pod from the "
            "Connections page first, or choose another account."
        )


def _installations(pod_sdk: Any) -> list[dict[str, Any]]:
    return [
        item
        for item in list_items(pod_sdk.connectors.auth_configs.list(limit=200))
        if str(item.get("status") or "ACTIVE").upper() == "ACTIVE"
    ]


def _choose_installation(
    connector: BundleConnector,
    candidates: list[dict[str, Any]],
    chosen: str | None,
    *,
    create: bool,
) -> str | None:
    if chosen:
        for item in candidates:
            if chosen in (str(item.get("id")), str(item.get("name"))):
                return str(item["id"])
        raise ValueError(
            f"connector '{connector.slug}': '{chosen}' is not an installation of "
            f"{connector.connector} in this organization"
        )
    if candidates:
        ordered = sorted(candidates, key=lambda item: not item.get("is_default"))
        return str(ordered[0]["id"])
    if create:
        return None
    raise ValueError(
        f"connector '{connector.slug}': your organization has no "
        f"{connector.connector} installation. Install it on the Connectors page, "
        f"choose one with --connector {connector.slug}=<installation>, or pass "
        "--create-installations to install Lemma's app for it."
    )


def plan_pod_connectors(
    client: Any,
    *,
    pod_id: str,
    source_dir: Path,
    installations: Mapping[str, str] | None = None,
    share_accounts: Mapping[str, str] | None = None,
    create_installations: bool = False,
) -> tuple[Any, list[_Planned], list[str]]:
    """Check every bundled connector and choice; write nothing.

    Returns the org-scoped pod client, what to declare, and the slugs the pod
    already declares for the same connector (kept as the pod has them).
    """
    installations = dict(installations or {})
    share_accounts = dict(share_accounts or {})
    connectors = load_bundle_connectors(source_dir)
    slugs = {connector.slug for connector in connectors}
    unknown = sorted((set(installations) | set(share_accounts)) - slugs)
    if unknown:
        raise ValueError(
            f"No connector named {', '.join(unknown)} in this bundle "
            f"(it declares: {', '.join(sorted(slugs)) or 'none'})."
        )
    if not connectors:
        return None, [], []
    # The pod's own organization: its installations are the ones to map to,
    # whatever organization the CLI happens to be pointed at.
    pod = to_plain(client.pods.get(pod_id))
    pod_sdk = client.pod(pod_id, org_id=str(pod.get("organization_id") or "") or None)
    declared = {
        str(item.get("slug")): (
            str(item.get("connector_id") or ""),
            str(item.get("mode") or "").upper(),
        )
        for item in list_items(pod_sdk.connectors.pod_connectors())
    }
    org_installations = _installations(pod_sdk)
    in_pod = _pod_accounts_by_account(pod_sdk) if share_accounts else {}
    planned: list[_Planned] = []
    kept: list[str] = []
    for connector in connectors:
        existing = declared.get(connector.slug)
        if existing is not None:
            conflict = _declared_conflict(connector, *existing)
            if conflict:
                raise ValueError(conflict)
            kept.append(connector.slug)
            continue
        share = share_accounts.get(connector.slug)
        if share and not connector.is_shared:
            raise ValueError(
                f"connector '{connector.slug}' is per member; only a SHARED "
                "connector runs as a shared account"
            )
        if share:
            _check_share(connector, share, in_pod=in_pod)
        candidates = [
            item
            for item in org_installations
            if str(item.get("connector_id")) == connector.connector
        ]
        planned.append(
            _Planned(
                connector=connector,
                auth_config_id=_choose_installation(
                    connector,
                    candidates,
                    installations.get(connector.slug),
                    create=create_installations,
                ),
                share_account_id=share,
            )
        )
    return pod_sdk, planned, kept


def _created_installation(pod_sdk: Any, connector: BundleConnector) -> str:
    from lemma_sdk.openapi_client.models import AuthConfigCreateSchema

    created = to_plain(
        pod_sdk.connectors.auth_configs.create(
            AuthConfigCreateSchema.from_dict(
                {"connector_id": connector.connector, "config_source": "SYSTEM_DEFAULT"}
            )
        )
    )
    return str(created["id"])


def declare_pod_connectors(
    pod_sdk: Any,
    planned: list[_Planned],
    *,
    on_notice: Callable[[str], None],
) -> list[str]:
    """Declare what :func:`plan_pod_connectors` checked; returns summary lines."""
    summary: list[str] = []
    for item in planned:
        connector = item.connector
        auth_config_id = item.auth_config_id or _created_installation(
            pod_sdk, connector
        )
        shared_pod_account_id = None
        undo: Callable[[], None] | None = None
        if item.share_account_id:
            shared_pod_account_id, undo = _share_with_the_pod(
                pod_sdk, item.share_account_id
            )
        try:
            pod_sdk.connectors.declare_pod_connector(
                connector.slug,
                auth_config_id,
                connector.mode,
                shared_pod_account_id=shared_pod_account_id,
                required=connector.required,
                purpose=connector.purpose,
            )
        except Exception:
            # The connector was refused: the account goes back to how it was,
            # rather than staying shared with the pod for nothing.
            if undo is not None:
                undo()
            raise
        if connector.visibility != "POD":
            pod_sdk.connectors.update_pod_connector(
                connector.slug, {"visibility": connector.visibility}
            )
        if connector.is_shared and shared_pod_account_id is None:
            on_notice(
                f"connector '{connector.slug}' ({connector.connector}) is shared "
                "and imported needing an account: share one from the pod's "
                f"Connections page, or re-import with --share-account "
                f"{connector.slug}=<your account id>."
            )
        elif connector.required and not connector.is_shared:
            on_notice(
                f"connector '{connector.slug}' ({connector.connector}) is per "
                "member and required: members will be asked to connect their own."
            )
        summary.append(f"created:{connector.slug}")
    return summary


def _share_with_the_pod(
    pod_sdk: Any, account_id: str
) -> tuple[str, Callable[[], None] | None]:
    """One of your accounts, shared with the whole pod; and how to undo that.

    Already in the pod -- added by hand, or on your first use of it there -- it
    is widened rather than added again, which the pod would refuse. Already
    shared with the pod, there is nothing to undo.
    """
    connectors = pod_sdk.connectors
    existing = _pod_accounts_by_account(pod_sdk).get(account_id)
    if existing is None:
        added = to_plain(connectors.add_pod_account(account_id, visibility="POD"))
        added_id = str(added["id"])
        return added_id, lambda: connectors.remove_pod_account(added_id)
    pod_account_id = str(existing["id"])
    if str(existing.get("visibility") or "").upper() == "POD":
        return pod_account_id, None
    connectors.update_pod_account(pod_account_id, {"visibility": "POD"})
    return pod_account_id, lambda: connectors.update_pod_account(
        pod_account_id, {"visibility": "PERSONAL"}
    )
