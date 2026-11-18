"""The app a bundle's pod opens on, carried by the CLI's own import.

`lemma pods import` applies a bundle from here, so it does what the server's
import does with ``pod.json -> home_app``: once the apps have landed, it sets
the app the pod opens on -- only on a pod that has none and had no apps before
this import. A pod that already opens somewhere keeps it, with an advisory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lemma_pod_bundle.jsonc import loads_jsonc
from lemma_sdk import Lemma
from lemma_sdk.errors import LemmaAPIError

from ..cli_core.io import list_items, to_plain
from ..cli_core.payload import build_request


def bundle_home_app(source_dir: Path) -> str | None:
    """``pod.json -> home_app``, or None when the bundle names none."""
    path = source_dir / "pod.json"
    if not path.is_file():
        return None
    try:
        manifest = loads_jsonc(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    name = manifest.get("home_app") if isinstance(manifest, dict) else None
    if not isinstance(name, str):
        return None
    return name.strip() or None


def _chosen(client: Lemma, pod_id: str) -> str | None:
    pod: dict[str, Any] = to_plain(client.pods.get(pod_id)) or {}
    config = pod.get("config")
    home = config.get("home_app") if isinstance(config, dict) else None
    return home if isinstance(home, str) and home else None


def _brings(source_dir: Path | None, wanted: str) -> bool:
    """Whether the bundle has the app it says it opens on."""
    return source_dir is None or (source_dir / "apps" / wanted).is_dir()


def plan_home_app(
    client: Lemma,
    pod_id: str,
    wanted: str | None,
    *,
    apps_before: set[str],
    source_dir: Path | None = None,
) -> list[str]:
    """The dry run's line for it, in the summary's ``action:name`` shape."""
    if wanted is None:
        return []
    chosen = _chosen(client, pod_id)
    if chosen == wanted:
        return [f"same:{wanted}"]
    if chosen is not None:
        return [f"kept:{chosen}"]
    if wanted not in apps_before and not _brings(source_dir, wanted):
        return ["missing:" + wanted]
    if apps_before - {wanted}:
        return ["kept:first-app"]
    return [f"set:{wanted}"]


def carry_home_app(
    client: Lemma, pod_id: str, wanted: str | None, *, apps_before: set[str]
) -> tuple[list[str], list[str]]:
    """Set it on the pod; answers the summary lines and any advisories."""
    from lemma_sdk.openapi_client.models.pod_update_request import PodUpdateRequest

    if wanted is None:
        return [], []
    # Everything else has landed by now: a failed read or write here is said,
    # never a reason to call the whole import failed.
    try:
        chosen = _chosen(client, pod_id)
        apps = {
            str(item.get("name"))
            for item in list_items(client.pod(pod_id).apps.list(limit=1000))
            if isinstance(item, dict) and item.get("name")
        }
    except LemmaAPIError as exc:
        return [], [f"The app this pod opens on was not set: {exc.message}"]
    if chosen == wanted:
        return [f"same:{wanted}"], []
    if chosen is not None:
        kept = (
            f"Kept the app this pod opens on ('{chosen}'); the bundle's "
            f"('{wanted}') was not set."
        )
        return [f"kept:{chosen}"], [kept]
    if wanted not in apps:
        missing = (
            f"This bundle opens on an app called '{wanted}', which it does not "
            "bring; the pod opens on its first app instead."
        )
        return [], [missing]
    if apps_before - {wanted}:
        had_apps = (
            "This pod already had apps, so it still opens on its first one; "
            f"the bundle's ('{wanted}') was not set."
        )
        return ["kept:first-app"], [had_apps]
    try:
        client.pods.update(
            pod_id, build_request(PodUpdateRequest, {"config": {"home_app": wanted}})
        )
    except LemmaAPIError as exc:
        return [], [f"The app this pod opens on was not set: {exc.message}"]
    return [f"set:{wanted}"], []
