"""Deciders in a bundle import: how the plan reads them and how a step saves one.

Split out of ``plan_builder.py`` and ``applier.py`` for the reason
``surface_apply.py`` was: both sit at the 600-line ceiling the architecture
ratchet sets.

A decider travels as its name and its current definition and nothing else (see
``lemma_pod_bundle.normalize._normalize_decider_payload``): its examples are
people's answers about their own data, and they never leave the pod. A step
creates the decider, or saves the definition as its next version, through the
decisions contract. When the pod's decider already has exactly this definition
the step does nothing, so importing an unchanged bundle again -- or replaying a
step after a crash -- saves no version.

Deciders are planned straight after tables, before anything that can name one:
a workflow's DECISION step, a schedule, an agent's ``decider:<name>:execute``
grant.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from lemma_pod_bundle import load_resource_payload
from pydantic import ValidationError

from app.core.authorization.context import ResourceRef, ResourceType
from app.core.authorization.permissions import Permissions
from app.modules.pod_bundle.domain.errors import BundleInvalidError
from app.modules.pod_bundle.domain.state import (
    PlanStep,
    StepAction,
    StepKind,
    StepStatus,
)

if TYPE_CHECKING:
    from app.modules.decisions.contracts.shapes import DeciderDefinition

DECIDERS_DIR = "deciders"
#: Past any pod a bundle is for: the exporters list at most this many of each
#: resource type, and say so when they reach it.
MOST_DECIDERS = 1_000


@dataclass(frozen=True, slots=True)
class DeciderSnapshot:
    """A pod's decider as it is now: what importing one would change."""

    id: UUID
    version: int
    definition: DeciderDefinition


class ExistingDeciders(Protocol):
    async def deciders(self) -> Mapping[str, DeciderSnapshot]: ...


class MayRequire(Protocol):
    """The part of an authorization ``Context`` a step asks."""

    async def require(
        self, permission_id: str, resource: ResourceRef | None = None
    ) -> None: ...


async def pod_deciders(pod_id: UUID) -> dict[str, DeciderSnapshot]:
    """The pod's deciders by name, as the plan compares a bundle against them."""
    from app.modules.decisions.contracts.deciders import list_deciders

    found = await list_deciders(pod_id=pod_id, limit=MOST_DECIDERS)
    return {
        decider.name: DeciderSnapshot(
            id=decider.id, version=decider.version, definition=decider.definition
        )
        for decider in found
    }


async def plan_deciders(
    bundle_root: Path, existing: ExistingDeciders
) -> list[PlanStep]:
    """One step per bundled decider, read against the pod's own.

    CREATE for a new name; UPDATE when the definition differs, which saves the
    next version and keeps the old ones; SKIP when the pod's decider already
    has exactly this definition. ``detail.version`` is the version the decider
    is at once the step has run. A definition no pod could save refuses the
    whole plan, before anything is written.
    """
    dirs = decider_dirs(bundle_root)
    if not dirs:
        return []
    current = await existing.deciders()
    return [_plan_step(path, current.get(path.name)) for path in dirs]


def decider_dirs(bundle_root: Path) -> list[Path]:
    """Every ``deciders/<name>/``, sorted by name.

    A manifest left loose at ``deciders/<name>.json`` is refused rather than
    passed over. Every reader of a bundle looks for ``<type>/<name>/<name>.json``,
    so a file there would be imported by nothing and noticed by nobody.
    """
    type_dir = bundle_root / DECIDERS_DIR
    if not type_dir.is_dir():
        return []
    loose = sorted(
        path.name
        for path in type_dir.iterdir()
        if path.is_file() and path.suffix == ".json"
    )
    if loose:
        raise BundleInvalidError(
            "A decider lives at deciders/<name>/<name>.json. Move "
            + ", ".join(f"deciders/{name}" for name in loose)
            + " into a folder of the decider's own name."
        )
    return sorted(
        (path for path in type_dir.iterdir() if path.is_dir()),
        key=lambda path: path.name,
    )


def bundled_definition(name: str, payload: Mapping[str, object]) -> DeciderDefinition:
    """The bundle's definition for ``name``, or why no pod could save it."""
    from app.modules.decisions.contracts.shapes import (
        DeciderDefinition,
        check_decider_name,
    )

    try:
        check_decider_name(name)
    except ValueError as exc:
        raise BundleInvalidError(f"Decider '{name}': {exc}.") from exc
    try:
        return DeciderDefinition.model_validate(payload.get("definition"))
    except ValidationError as exc:
        raise BundleInvalidError(
            f"Decider '{name}' is not a definition a pod can save: {_problems(exc)}"
        ) from exc


def same_definition(left: DeciderDefinition, right: DeciderDefinition) -> bool:
    return left.model_dump(mode="json") == right.model_dump(mode="json")


async def apply_decider(
    step: PlanStep,
    load: Callable[[str, str], Mapping[str, object]],
    ctx: MayRequire,
    pod_id: UUID,
    user_id: UUID,
    /,
) -> None:
    """Create the decider or save its next version, as the person importing.

    Re-reads the pod rather than trusting the plan, like every other step, so a
    replay converges. Positional so that ``applier.py``, at its line ceiling,
    can hand a step over in one line; the order is the applier's own.
    """
    from app.modules.decisions.contracts.deciders import define_decider, get_decider

    definition = bundled_definition(step.name, load(DECIDERS_DIR, step.name))
    current = await get_decider(pod_id=pod_id, name=step.name)
    if current is not None and same_definition(current.definition, definition):
        return
    if current is None:
        await ctx.require(Permissions.DECIDER_CREATE, ResourceRef.pod(pod_id))
    else:
        await ctx.require(
            Permissions.DECIDER_UPDATE,
            ResourceRef(
                resource_type=ResourceType.DECIDER,
                resource_id=current.id,
                pod_id=pod_id,
            ),
        )
    await define_decider(
        pod_id=pod_id, user_id=user_id, name=step.name, definition=definition
    )


def _plan_step(resource_dir: Path, current: DeciderSnapshot | None) -> PlanStep:
    name = resource_dir.name
    definition = bundled_definition(name, _manifest(resource_dir))
    if current is None:
        return PlanStep(
            index=0,
            kind=StepKind.DECIDER,
            name=name,
            action=StepAction.CREATE,
            detail={"version": 1},
        )
    if same_definition(current.definition, definition):
        return PlanStep(
            index=0,
            kind=StepKind.DECIDER,
            name=name,
            action=StepAction.SKIP,
            status=StepStatus.SKIPPED,
            error="this pod's decider already has this definition",
            detail={"version": current.version},
        )
    return PlanStep(
        index=0,
        kind=StepKind.DECIDER,
        name=name,
        action=StepAction.UPDATE,
        detail={"version": current.version + 1},
    )


def _manifest(resource_dir: Path) -> Mapping[str, object]:
    """The bundled manifest, or a refusal that names it.

    Not the exception's own text: it carries the path the bundle was unpacked
    to, which means nothing to the person who uploaded it.
    """
    name = resource_dir.name
    try:
        return load_resource_payload(resource_dir, name, resource_type=DECIDERS_DIR)
    except OSError as exc:
        raise BundleInvalidError(
            f"deciders/{name}/{name}.json, or a file it names, is not in the bundle."
        ) from exc
    except ValueError as exc:
        raise BundleInvalidError(
            f"deciders/{name}/{name}.json is not a JSON object."
        ) from exc


def _problems(exc: ValidationError) -> str:
    """Where a definition is wrong, the first few places pydantic names."""
    errors = exc.errors(include_url=False)
    shown = [
        f"{'.'.join(str(part) for part in error['loc']) or 'definition'}: "
        f"{error['msg']}"
        for error in errors[:3]
    ]
    rest = len(errors) - len(shown)
    return "; ".join(shown) + (f" (and {rest} more)" if rest > 0 else "")
