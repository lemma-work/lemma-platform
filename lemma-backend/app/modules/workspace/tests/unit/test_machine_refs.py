"""One ref grammar: every spelling a caller may send, and the one Lemma writes.

``pod:<pod>:<name>`` is canonical -- the pod by id or by name -- because it is
the form that fits in one URL path segment. ``pod:<pod>/<name>`` is read and
never written. A pod *name* resolves among the pods the caller is in, which a
REST caller has no pod in context to narrow; before, it resolved only against
``caller.pod_id`` and so 404'd from the CLI for every pod.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.modules.workspace.contracts.machine_refs import parse_machine_ref
from app.modules.workspace.domain.machine import MachineRef, MachineRefKind
from app.modules.workspace.domain.machine_access import (
    CallerKind,
    MachineCaller,
    PodAccess,
)
from app.modules.workspace.domain.machine_errors import MachineNotFound
from app.modules.workspace.services.machine_resolver import MachineResolver

pytestmark = pytest.mark.unit

POD = uuid4()
OTHER_POD = uuid4()


@pytest.mark.parametrize(
    ("raw", "pod", "name", "canonical"),
    [
        (f"pod:{POD}:qa", str(POD), "qa", f"pod:{POD}:qa"),
        (f"pod:{POD}/qa", str(POD), "qa", f"pod:{POD}:qa"),
        ("pod:research:qa", "research", "qa", "pod:research:qa"),
        ("pod:research/qa", "research", "qa", "pod:research:qa"),
        ("pod:qa", None, "qa", "pod:qa"),
    ],
)
def test_every_spelling_parses_and_writes_back_canonical(raw, pod, name, canonical):
    parsed = MachineRef.parse(raw)

    assert parsed.kind is MachineRefKind.POD
    assert (parsed.pod, parsed.name) == (pod, name)
    assert str(parsed) == canonical
    assert MachineRef.parse(str(parsed)) == parsed
    assert parse_machine_ref(raw) == canonical


@pytest.mark.parametrize("raw", ["me", "mine:mac", str(POD)])
def test_the_other_kinds_round_trip(raw):
    assert str(MachineRef.parse(raw)) == raw


def test_no_canonical_ref_holds_a_slash():
    """The slash form cannot sit in one path segment, so nothing Lemma hands
    out may use it."""
    assert "/" not in str(MachineRef(MachineRefKind.POD, name="qa", pod=str(POD)))


@pytest.mark.parametrize("raw", ["pod:", "pod:a/b/c", "pod:Bad_Name", "nonsense"])
def test_what_is_not_a_ref_says_so(raw):
    with pytest.raises(ValueError):
        MachineRef.parse(raw)


def test_the_refusal_names_the_canonical_form():
    with pytest.raises(ValueError, match="pod:<pod>:<name>"):
        MachineRef.parse("nonsense")


# --- naming the pod --------------------------------------------------------------


class _Facts:
    def __init__(self, named: dict[str, list[UUID]]) -> None:
        self.named = named

    async def answers_member(self, caller: MachineCaller) -> bool:
        return False

    async def pod_access(self, caller, machine) -> PodAccess:
        return PodAccess()

    async def pods_named(self, caller: MachineCaller, name: str) -> list[UUID]:
        return self.named.get(name.lower(), [])


class _NoDatabase:
    session = None


def _resolver(named: dict[str, list[UUID]]) -> MachineResolver:
    return MachineResolver(_NoDatabase(), facts=_Facts(named))  # type: ignore[arg-type]


def _person(pod: UUID | None = None) -> MachineCaller:
    return MachineCaller(CallerKind.USER, user_id=uuid4(), pod_id=pod)


async def test_a_pod_name_resolves_with_no_pod_in_context():
    """The CLI's case: a person over REST, naming a pod they are in."""
    ref = MachineRef.parse("pod:research:qa")

    assert await _resolver({"research": [POD]})._pod_of(ref, _person()) == POD


async def test_the_slash_form_resolves_the_same_way():
    ref = MachineRef.parse("pod:research/qa")

    assert await _resolver({"research": [POD]})._pod_of(ref, _person()) == POD


async def test_a_pod_id_needs_no_lookup():
    ref = MachineRef.parse(f"pod:{POD}:qa")

    assert await _resolver({})._pod_of(ref, _person()) == POD


async def test_the_bare_form_is_the_pod_in_context():
    ref = MachineRef.parse("pod:qa")

    assert await _resolver({})._pod_of(ref, _person(POD)) == POD
    assert await _resolver({})._pod_of(ref, _person()) is None


async def test_a_pod_the_caller_is_not_in_is_not_found():
    ref = MachineRef.parse("pod:elsewhere:qa")

    assert await _resolver({"research": [POD]})._pod_of(ref, _person()) is None


async def test_a_shared_name_is_refused_with_the_way_out():
    ref = MachineRef.parse("pod:research:qa")

    with pytest.raises(MachineNotFound) as refused:
        await _resolver({"research": [POD, OTHER_POD]})._pod_of(ref, _person())

    assert refused.value.status_code == 404
    assert "pod:<pod-id>:qa" in refused.value.message


async def test_a_shared_name_means_the_pod_in_context_when_it_is_one_of_them():
    ref = MachineRef.parse("pod:research:qa")
    resolver = _resolver({"research": [POD, OTHER_POD]})

    assert await resolver._pod_of(ref, _person(OTHER_POD)) == OTHER_POD
