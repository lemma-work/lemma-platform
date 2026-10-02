"""A pod's deciders in a bundle: exported, planned and imported over the API.

A decider travels as ``deciders/<name>/<name>.json`` holding its name and its
current definition, and nothing it has learned
(``lemma_pod_bundle.normalize._normalize_decider_payload``): its examples are
people's answers about their own data, and they stay in the pod they were
given in.

The generated SDK has no decider operations yet, so these calls go through the
SDK's raw request path -- ``Pod.request``, its documented escape hatch -- to
``GET``/``POST /pods/{pod_id}/deciders`` and ``PUT /pods/{pod_id}/deciders/
{name}``. :class:`DeciderRoutes` is the one place to change once the SDK is
regenerated with them.

Definitions are compared as written. One saved by an export matches the pod's
exactly, so importing it again changes nothing; a hand-written one that leaves
out what the server fills in saves a new version each time, since only the
server knows its defaults. The server-side import compares after validation
and has no such gap.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

from lemma_pod_bundle.layout import _write_json, load_resource_payload
from lemma_pod_bundle.normalize import (
    BundleValidationIssue,
    _normalize_decider_payload,
    _without_nulls,
)
from lemma_sdk.errors import LemmaAPIError

from ..cli_core.state import err_console as console

DECIDERS_DIR = "deciders"
#: The most `GET /pods/{pod_id}/deciders` answers with at once. It has no next
#: page, so an export that reaches it says so.
LIST_LIMIT = 500

type JsonObject = dict[str, object]


class RawRequests(Protocol):
    """What :class:`DeciderRoutes` needs of an SDK ``Pod``."""

    @property
    def pod_id(self) -> str: ...

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, object] | None = ...,
        json: object = ...,
    ) -> object: ...


class DeciderRoutes:
    """``/pods/{pod_id}/deciders``, over the SDK's raw request path."""

    def __init__(self, pod: RawRequests) -> None:
        self._pod = pod
        self._path = f"/pods/{pod.pod_id}/deciders"

    def list(self) -> list[JsonObject]:
        listed = self._pod.request("GET", self._path, params={"limit": LIST_LIMIT})
        items = listed.get("items") if isinstance(listed, dict) else None
        return [item for item in items or [] if isinstance(item, dict)]

    def create(self, name: str, definition: object) -> None:
        self._pod.request(
            "POST", self._path, json={"name": name, "definition": definition}
        )

    def update(self, name: str, definition: object) -> None:
        self._pod.request(
            "PUT",
            f"{self._path}/{quote(name, safe='')}",
            json={"definition": definition},
        )


def export_deciders(
    routes: DeciderRoutes,
    bundle_root: Path,
    *,
    wanted: Callable[[JsonObject], bool],
    warnings: list[str],
) -> int:
    """Write each decider the export selects, and say how many.

    A server that has no deciders, or a person who may not read them, gets a
    bundle without them and a warning saying which -- not a failed export.
    """
    try:
        listed = routes.list()
    except LemmaAPIError as exc:
        reason = _unreadable(exc)
        if reason is None:
            raise
        warnings.append(f"deciders were left out: {reason}")
        return 0
    if len(listed) >= LIST_LIMIT:
        warnings.append(
            f"only the first {LIST_LIMIT} deciders, by name, were exported: the "
            "API lists no more than that at once."
        )
    written = 0
    for item in sorted(listed, key=lambda item: str(item.get("name") or "")):
        name = str(item.get("name") or "")
        if not name or not wanted(item):
            continue
        resource_dir = bundle_root / DECIDERS_DIR / name
        resource_dir.mkdir(parents=True, exist_ok=True)
        _write_json(resource_dir / f"{name}.json", _normalize_decider_payload(item))
        written += 1
    return written


def decider_issues(
    resource_dir: Path, payload: JsonObject
) -> list[BundleValidationIssue]:
    """The static checks a decider manifest fails, against its own file."""
    from .scaffold import validate_decider

    path = str(resource_dir / f"{resource_dir.name}.json")
    return [
        BundleValidationIssue(path=path, message=message)
        for message in validate_decider({"name": resource_dir.name, **payload})
    ]


def loose_decider_files(source_dir: Path) -> list[BundleValidationIssue]:
    """A manifest at ``deciders/<name>.json`` rather than in its own folder.

    Every reader of a bundle looks for ``<type>/<name>/<name>.json``, so such a
    file would be imported by nothing and noticed by nobody.
    """
    type_dir = source_dir / DECIDERS_DIR
    if not type_dir.is_dir():
        return []
    return [
        BundleValidationIssue(
            path=str(path),
            message=(
                "A decider lives at deciders/<name>/<name>.json. Move this file "
                "into a folder of the decider's own name."
            ),
        )
        for path in sorted(type_dir.glob("*.json"))
        if path.is_file()
    ]


def plan_deciders(
    routes: DeciderRoutes, resource_dirs: list[Path], *, upsert: bool
) -> tuple[list[str], list[BundleValidationIssue]]:
    """What importing the bundle's deciders would do: ``created``, ``updated``
    (a new version, the old kept) or ``unchanged``, and anything that stops it."""
    if not resource_dirs:
        return [], []
    try:
        existing = _by_name(routes.list())
    except LemmaAPIError as exc:
        reason = _unreadable(exc)
        if reason is None:
            raise
        return [], [
            BundleValidationIssue(
                path=str(resource_dirs[0].parent),
                message=f"The bundle's deciders cannot be imported: {reason}",
            )
        ]
    entries: list[str] = []
    issues: list[BundleValidationIssue] = []
    for resource_dir in resource_dirs:
        name = resource_dir.name
        current = existing.get(name)
        if current is None:
            entries.append(f"created:{name}")
        elif not upsert:
            issues.append(
                BundleValidationIssue(
                    path=str(resource_dir), message=f"Decider already exists: {name}"
                )
            )
        elif _unchanged(current, _bundled_definition(resource_dir)):
            entries.append(f"unchanged:{name}")
        else:
            entries.append(f"updated:{name}")
    return entries, issues


def import_deciders(
    routes: DeciderRoutes,
    resource_dirs: list[Path],
    *,
    prepare: Callable[[JsonObject], JsonObject],
    upsert: bool,
) -> list[str]:
    """Create each decider, or save its next version, and say which.

    ``prepare`` resolves the bundle's ``${name}`` variables, as it does for every
    other resource. A decider whose definition already matches is left alone.
    """
    if not resource_dirs:
        return []
    existing = _by_name(routes.list())
    entries: list[str] = []
    for resource_dir in resource_dirs:
        name = resource_dir.name
        definition = prepare(
            load_resource_payload(resource_dir, name, resource_type=DECIDERS_DIR)
        ).get("definition")
        current = existing.get(name)
        if current is None:
            console.print(f"[cyan]decider[/cyan] creating {name}")
            routes.create(name, definition)
            action = "created"
        elif not upsert:
            raise ValueError(
                f"Decider already exists and --no-upsert was requested: {name}"
            )
        elif _unchanged(current, definition):
            action = "unchanged"
        else:
            console.print(f"[cyan]decider[/cyan] saving a new version of {name}")
            routes.update(name, definition)
            action = "updated"
        console.print(f"[green]decider[/green] {action} {name}")
        entries.append(f"{action}:{name}")
    return entries


def decider_names_for_grants(routes: DeciderRoutes) -> list[JsonObject]:
    """The pod's deciders, for checking the grants that name one.

    None on a server without deciders, so a grant naming one is reported as
    unknown -- which, on that server, it is.
    """
    try:
        return routes.list()
    except LemmaAPIError as exc:
        if exc.status_code == 404:
            return []
        raise


def _by_name(items: list[JsonObject]) -> dict[str, JsonObject]:
    return {str(item.get("name")): item for item in items if item.get("name")}


def _bundled_definition(resource_dir: Path) -> object:
    """The bundle's definition, or None when the manifest cannot be read --
    which the plan has already reported against the file."""
    try:
        payload = load_resource_payload(
            resource_dir, resource_dir.name, resource_type=DECIDERS_DIR
        )
    except OSError, ValueError:
        return None
    return payload.get("definition")


def _unchanged(current: JsonObject, definition: object) -> bool:
    return definition is not None and _without_nulls(
        current.get("definition")
    ) == _without_nulls(definition)


def _unreadable(exc: LemmaAPIError) -> str | None:
    """Why the pod's deciders cannot be listed, when it is not a failure."""
    if exc.status_code == 404:
        return "this server has no deciders API."
    if exc.status_code == 403:
        return "you may not read this pod's deciders."
    return None
