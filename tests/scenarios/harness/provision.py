"""Build the standing tenant on a deployment, or put it back the way it was.

Run once per environment, never as part of a run. That separation is the whole
design: a run signs the cast **in**, and signing in passes none of the gates a
real deployment keeps in front of registration. Only this script ever registers
anybody, and it is meant to be run by a person who can see what it did.

Idempotent by construction — every step asks what is already there and changes
only what disagrees with `harness/tenant.py`. Run it twice and the second run
reports that it had nothing to do.

    uv run python -m harness.provision --base-url https://dev.example
    uv run python -m harness.provision --base-url https://dev.example --reset

`--reset` is the other half: it removes what runs have left behind — matched on
the `scn` mark, so it can only ever touch things a run made — and puts the
cast's roles back to what this module says they should be. For when a run dies
partway through and leaves somebody promoted.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Callable
from typing import Any

from harness import environment, tenant
from harness.run import current, made_by_a_run
from harness.world import Person, World

JSON = dict[str, Any]

BASE_URL_SETTING = "SCENARIOS_BASE_URL"


class Ledger:
    """What was done, so the operator can see it rather than infer it."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.changed = 0

    def did(self, what: str) -> None:
        self.lines.append(f"  + {what}")
        self.changed += 1

    def already(self, what: str) -> None:
        self.lines.append(f"    {what}")

    def report(self, title: str) -> str:
        body = "\n".join(self.lines)
        tail = (
            f"{self.changed} change{'' if self.changed == 1 else 's'}"
            if self.changed
            else "nothing to do — the tenant already matches harness/tenant.py"
        )
        return f"{title}\n{body}\n\n{tail}"


def owner_of(company: tenant.Company) -> tenant.Colleague:
    for colleague in tenant.CAST:
        if colleague.company is company and colleague.role == "ORG_OWNER":
            return colleague
    raise AssertionError(f"{company.name} has no owner declared in harness/tenant.py")


async def provision(base_url: str, *, reset: bool = False) -> str:
    target = environment.describe(base_url)
    environment.confirm_writable(target)
    ledger = Ledger()

    world = World(base_url=base_url)
    try:
        people = {
            colleague.label: world.arriving(colleague.label, colleague.email)
            for colleague in tenant.CAST
        }

        for colleague in tenant.CAST:
            person = people[colleague.label]
            if await person.arrives():
                ledger.did(f"registered {colleague.full_name} <{colleague.email}>")
            else:
                ledger.already(f"{colleague.full_name} already has an account")

        companies: dict[str, JSON] = {}
        for company in tenant.COMPANIES:
            owner = people[owner_of(company).label]
            companies[company.key] = await _company(owner, company, ledger)

        for colleague in tenant.CAST:
            if colleague.role == "ORG_OWNER":
                continue
            await _standing(
                owner=people[owner_of(colleague.company).label],
                person=people[colleague.label],
                colleague=colleague,
                company=companies[colleague.company.key],
                ledger=ledger,
            )

        boss = people[owner_of(tenant.VANTAGE).label]
        boss.organization = companies[tenant.VANTAGE.key]
        administrator = people["daniel"]
        for standing_pod in tenant.STANDING_PODS:
            pod = await _pod(boss, standing_pod, ledger)
            await _administers(boss, administrator, pod, ledger)
            if reset:
                await _clear_run_debris(boss, pod, ledger, mine=made_by_a_run)
        if reset:
            await _clear_run_pods(boss, ledger)

        return ledger.report(
            f"{'Reset' if reset else 'Provisioned'} {base_url} "
            f"({target.environment})"
        )
    finally:
        await world.aclose()


async def _company(owner: Person, company: tenant.Company, ledger: Ledger) -> JSON:
    for existing in await owner.organizations():
        if existing.get("name") == company.name:
            ledger.already(f"{company.name} is there")
            owner.organization = existing
            return existing
    made = await owner.creates_an_organization(named=company.name, standing=True)
    ledger.did(f"created {company.name}, owned by {owner.label}")
    return made


async def _standing(
    *,
    owner: Person,
    person: Person,
    colleague: tenant.Colleague,
    company: JSON,
    ledger: Ledger,
) -> None:
    members = await owner.members_of(company)
    mine = next(
        (m for m in members if str(m.get("user_id")) == str(person.user_id)), None
    )
    if mine is None:
        invitation = await owner.invites(person, to=company, as_role=colleague.role)
        await person.accepts(invitation)
        ledger.did(
            f"{colleague.full_name} joined {colleague.company.name} "
            f"as {colleague.role}"
        )
        return
    if str(mine.get("role")) != colleague.role:
        was = mine.get("role")
        await owner.changes_role(person, to=colleague.role, in_organization=company)
        ledger.did(
            f"{colleague.full_name} put back to {colleague.role} (was {was})"
        )
        return
    ledger.already(f"{colleague.full_name} is {colleague.role}")


async def _pod(owner: Person, standing: tenant.StandingPod, ledger: Ledger) -> JSON:
    before = {pod.get("name") for pod in await owner.pods_in(owner.organization)}
    pod = await owner.works_in(standing.name)
    if standing.name in before:
        ledger.already(f"pod {standing.name!r} is there")
    else:
        ledger.did(f"created pod {standing.name!r} — {standing.holds}")
    return pod


async def _administers(
    owner: Person, administrator: Person, pod: JSON, ledger: Ledger
) -> None:
    members = await owner.members_of_pod(pod)
    already = any(
        str(member.get("user_id")) == str(administrator.user_id) for member in members
    )
    if already:
        return
    await owner.adds(administrator, to_pod=pod, as_role="POD_ADMIN")
    ledger.did(f"{administrator.label} administers {pod.get('name')!r}")


async def _clear_run_debris(
    owner: Person, pod: JSON, ledger: Ledger, *, mine: Callable[[str], bool]
) -> None:
    """Delete what runs left in a standing pod, and nothing else.

    Matched on the `scn` mark rather than on shape, because this runs against a
    deployment where everything else in the pod is somebody's actual work. A
    reset that matched on shape would eventually take some of it.

    `mine` is the difference between the two callers. A run sweeping up after
    itself takes only its own, so two runs against the same tenant do not delete
    each other's work half way through. An operator running `--reset` takes
    every run's, which is the point of asking.

    Tables and the top level of the file tree. A file inside a run-made folder
    goes when the folder does; agents, schedules and workflows a run leaves
    behind are still there afterwards — said plainly, because a cleanup that
    quietly covers half of what it looks like it covers is worse than one that
    admits its edges.
    """
    for table in await owner.tables_in(pod):
        name = str(table.get("name", ""))
        if mine(name):
            await owner.deletes_table(name, in_pod=pod)
            ledger.did(f"removed table {name!r} from {pod.get('name')!r}")

    # Files matter more than they look: an unprocessable document retries for as
    # long as it exists, and a standing pod that accumulates a few runs' worth of
    # them starves document work for everything else in that pod. That is not
    # hygiene, it is the reason a later run sees a converter that never answers.
    for entry in _tree_entries(await owner.file_tree_of(pod)):
        path = str(entry.get("path") or "")
        name = str(entry.get("name") or "")
        if path and mine(name):
            await owner.deletes_file(path, in_pod=pod)
            ledger.did(f"removed {path!r} from {pod.get('name')!r}")


def _tree_entries(tree: Any) -> list[JSON]:
    """The top level of a pod's file tree, whatever envelope it arrives in."""
    if isinstance(tree, dict):
        for key in ("items", "children", "entries", "nodes"):
            found = tree.get(key)
            if isinstance(found, list):
                return [entry for entry in found if isinstance(entry, dict)]
    if isinstance(tree, list):
        return [entry for entry in tree if isinstance(entry, dict)]
    return []


async def sweep(base_url: str) -> str:
    """Remove what *this* run left in the standing pods.

    Called at the end of a session. Only this run's own leavings, so a second
    run working in the same tenant at the same time is untouched.
    """
    ledger = Ledger()
    world = World(base_url=base_url)
    try:
        owner = world.arriving(*_owner_credentials())
        await owner.signs_in()
        owner.organization = await _company_named(owner, tenant.VANTAGE.name)
        if owner.organization is None:
            return "nothing to sweep: the tenant is not provisioned here"
        # Pods first, deliberately. A pod this run made carries everything the
        # run put inside it, so removing one is worth more than any number of
        # individual deletes — and the sweep runs under a time budget, so the
        # order decides what gets done when there is not enough of it.
        await _clear_run_pods(owner, ledger, mine=current().made_this)
        standing = {pod.name for pod in tenant.STANDING_PODS}
        for pod in await owner.pods_in(owner.organization):
            if pod.get("name") in standing:
                await _clear_run_debris(owner, pod, ledger, mine=current().made_this)
        return ledger.report(f"Swept {current()} from {base_url}")
    finally:
        await world.aclose()


def _owner_credentials() -> tuple[str, str]:
    owner = owner_of(tenant.VANTAGE)
    return owner.label, owner.email


async def _company_named(owner: Person, name: str) -> JSON | None:
    for organization in await owner.organizations():
        if organization.get("name") == name:
            return organization
    return None


async def _clear_run_pods(
    owner: Person, ledger: Ledger, *, mine: Callable[[str], bool] = made_by_a_run
) -> None:
    """Delete pods a run made, which are the ones carrying the mark.

    A standing pod is never named through `run.name()`, so it can never match
    here — which is the property that makes this safe to point at a deployment.

    Note what this does not recover: a pod delete is a soft delete, and the
    pod's datastore schema stays in Postgres for good. Deleting the pod is
    still worth doing — it stops the pod being listed and stops its standing
    work — but a deployment that has run this suite for a year has a year of
    schemas in it, and no API can take them back.
    """
    for pod in await owner.pods_in(owner.organization):
        name = str(pod.get("name", ""))
        if mine(name):
            await owner.deletes_pod(pod)
            ledger.did(f"removed leftover pod {name!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--base-url",
        default=os.getenv(BASE_URL_SETTING, ""),
        help=f"the deployment to build the tenant on (or {BASE_URL_SETTING})",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="also clear what runs left behind and put the cast's roles back",
    )
    arguments = parser.parse_args(argv)
    if not arguments.base_url:
        parser.error(
            f"no deployment given. Pass --base-url, or set {BASE_URL_SETTING}. "
            f"There is no default on purpose: this script registers accounts "
            f"and creates organizations, and an organization cannot be deleted."
        )
    try:
        print(asyncio.run(provision(arguments.base_url, reset=arguments.reset)))
    except (environment.Unreachable, AssertionError) as stopped:
        print(f"provisioning stopped: {stopped}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
