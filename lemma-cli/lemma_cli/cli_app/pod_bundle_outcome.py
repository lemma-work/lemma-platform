"""What a bundle's pod owns, carried by the CLI's own import.

`lemma pods import` applies a bundle from here, resource by resource, so it
does what the server's import does with ``pod.json -> outcome``: set it once
everything else has landed, so its measure counts the tables the bundle
brought, and only on a pod that owns nothing yet. A pod that already owns
something keeps it. A measure the importer cannot read is dropped and the rest
kept, with an advisory saying why, as the server's import does with a warning.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lemma_pod_bundle.jsonc import loads_jsonc
from lemma_sdk import Lemma
from lemma_sdk.errors import LemmaAPIError

from ..cli_core.io import to_plain
from ..cli_core.payload import build_request

#: What a pod owns as a bundle carries it; ``since`` is the importer's to start.
_FIELDS = ("sentence", "kind", "target", "unit", "due", "measure")
#: The promise without its measure: a pod that owns this already owns the
#: bundle's, measured or not.
_PROMISE = ("sentence", "kind", "target", "unit", "due")


def bundle_outcome(source_dir: Path) -> dict[str, Any] | None:
    """``pod.json -> outcome``, or None when the bundle's pod owned nothing."""
    path = source_dir / "pod.json"
    if not path.is_file():
        return None
    try:
        manifest = loads_jsonc(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    block = manifest.get("outcome") if isinstance(manifest, dict) else None
    if not isinstance(block, dict) or not block.get("sentence"):
        return None
    return {key: block[key] for key in _FIELDS if block.get(key) is not None}


def _owned_now(client: Lemma, pod_id: str) -> dict[str, Any] | None:
    """What the pod owns now. An API from before a pod kept what it owns
    answers 404, and owns nothing as far as an import is concerned."""
    try:
        standing = to_plain(client.pods.outcome_standing(pod_id)) or {}
    except LemmaAPIError as exc:
        if exc.status_code == 404:
            return None
        raise
    owned = standing.get("outcome") if isinstance(standing, dict) else None
    return owned if isinstance(owned, dict) and owned.get("sentence") else None


def _same(owned: dict[str, Any], block: dict[str, Any]) -> bool:
    return all(owned.get(key) == block.get(key) for key in _PROMISE)


def plan_outcome(client: Lemma, pod_id: str, block: dict[str, Any] | None) -> list[str]:
    """The dry run's line for it, in the summary's ``action:name`` shape."""
    if block is None:
        return []
    owned = _owned_now(client, pod_id)
    if owned is None:
        return [f"set:{block['sentence']}"]
    if _same(owned, block):
        return [f"same:{block['sentence']}"]
    return [f"kept:{owned['sentence']}"]


def carry_outcome(
    client: Lemma, pod_id: str, block: dict[str, Any] | None
) -> tuple[list[str], list[str]]:
    """Set it on the pod; answers the summary lines and any advisories."""
    from lemma_sdk.openapi_client.models.outcome_input import OutcomeInput

    if block is None:
        return [], []
    owned = _owned_now(client, pod_id)
    if owned is not None:
        if _same(owned, block):
            return [f"same:{block['sentence']}"], []
        return [f"kept:{owned['sentence']}"], [
            f"Kept what this pod already owns ('{owned['sentence']}'); the "
            f"bundle's ('{block['sentence']}') was not set."
        ]
    try:
        client.pods.set_outcome(pod_id, build_request(OutcomeInput, block))
        return [f"set:{block['sentence']}"], []
    except LemmaAPIError as exc:
        if exc.status_code == 404:
            return [], [
                "This server does not keep what a pod owns, so the bundle's "
                f"('{block['sentence']}') was not set."
            ]
        if "measure" not in block:
            return [], [f"What this bundle's pod owns was not set: {exc.message}"]
        refusal = exc.message
    unmeasured = {key: value for key, value in block.items() if key != "measure"}
    try:
        client.pods.set_outcome(pod_id, build_request(OutcomeInput, unmeasured))
    except LemmaAPIError as exc:
        return [], [f"What this bundle's pod owns was not set: {exc.message}"]
    return [f"set:{block['sentence']}"], [
        f"Set what this pod owns ('{block['sentence']}') without its measure, "
        f"which could not be read ({refusal}). Set it again with "
        "`lemma pods owns set` once it reads."
    ]
