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

## Deployments this process cannot register the cast on by itself

Signing up is not always possible from here. A deployment can put a
proof-of-work challenge in front of it, or require a verification email to be
read back before anything else works — gates this module has no business
knowing how to get past, because publishing how would be indistinguishable
from documenting how to defeat them in bulk.

`--authenticate-with path/to/module.py:function` replaces *only* the "get the
cast signed in" half with an external one. Everything after that — building
the organizations, the pods, the memberships — is unchanged and still runs
here, because none of that is gated by anything the replacement needed to get
past. The function receives the `World` and must return every `tenant.CAST`
label mapped to a signed-in `Person` (`Person.api.authenticate(token)` and
`Person.user_id` set — the same state `signs_up`/`signs_in` leave behind).
What it does to get there is its own business: `IdentitySteps.signs_up`,
`.requests_email_verification` and the rest all accept `**kwargs`, reaching
the raw request, for exactly this — an external caller can attach whatever a
particular deployment demands without this module ever needing to know what
that was.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from harness import consent, environment, tenant
from harness.run import current, made_by_a_run
from harness.world import Person, World

JSON = dict[str, Any]

BASE_URL_SETTING = "SCENARIOS_BASE_URL"
AUTHENTICATE_WITH_SETTING = "SCENARIOS_AUTHENTICATE_WITH"

#: What an --authenticate-with function must return: every tenant.CAST label
#: mapped to a Person already in the state signs_up/signs_in leave one in.
Authenticator = Callable[[World], Awaitable[dict[str, Person]]]


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


async def provision(
    base_url: str, *, reset: bool = False, authenticate: Authenticator | None = None
) -> str:
    target = environment.describe(base_url)
    environment.confirm_writable(target)
    ledger = Ledger()

    world = World(base_url=base_url)
    try:
        if authenticate is not None:
            people = await authenticate(world)
            missing = {colleague.label for colleague in tenant.CAST} - set(people)
            if missing:
                raise AssertionError(
                    f"--authenticate-with did not return {sorted(missing)}; it "
                    f"must authenticate every label in harness/tenant.py's CAST"
                )
            for colleague in tenant.CAST:
                ledger.already(f"{colleague.full_name} authenticated externally")
        else:
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
            for company in tenant.COMPANIES:
                owner = people[owner_of(company).label]
                owner.organization = companies[company.key]
                await _clear_run_pods(owner, ledger)
                await _uninstall_run_connectors(owner, ledger, mine=made_by_a_run)
                await _only_the_cast(owner, ledger)

        written = ledger.report(
            f"{'Reset' if reset else 'Provisioned'} {base_url} "
            f"({target.environment})"
        )
        return written + await _still_needs_a_person(boss)
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
    entries = _tree_entries(await owner.file_tree_of(pod))
    # Deepest first: removing a folder takes what is inside it, so a child that
    # has already gone would otherwise 404 and stop the sweep on its way past.
    for entry in sorted(entries, key=lambda e: str(e.get("path", "")).count("/"), reverse=True):
        path = str(entry.get("path") or "")
        if not mine(str(entry.get("name") or "")):
            continue
        try:
            await owner.deletes_file(path, in_pod=pod)
        except AssertionError:
            continue  # already gone with its folder
        ledger.did(f"removed {path!r} from {pod.get('name')!r}")


def _tree_entries(tree: Any) -> list[JSON]:
    """Every entry in a pod's file tree, at any depth.

    All the way down, and that is not thoroughness for its own sake. A document
    no converter can read is retried for as long as it exists, and a worker
    doing that for a handful of them starves the agent runs everything else is
    waiting on — which shows up as unrelated scenarios timing out, several
    journeys away from the file that caused it. Walking only the top level left
    exactly those files behind whenever a run had put them in a folder.
    """
    found: list[JSON] = []
    _walk(tree, found)
    return found


def _walk(node: Any, found: list[JSON]) -> None:
    if isinstance(node, list):
        for item in node:
            _walk(item, found)
        return
    if not isinstance(node, dict):
        return
    if node.get("path") and node.get("name"):
        found.append(node)
    for key in ("items", "children", "entries", "nodes"):
        _walk(node.get(key), found)


async def sweep(base_url: str) -> str:
    """Remove what *this* run left in the standing pods.

    Called at the end of a session. Only this run's own leavings, so a second
    run working in the same tenant at the same time is untouched.
    """
    ledger = Ledger()
    world = World(base_url=base_url)
    try:
        # Both companies. Calder Retail is small — it exists so that "somebody
        # outside is refused" has a genuine outsider — but scenarios do make
        # pods in it, and a sweep that only looked at Vantage left them there.
        # A pod nobody clears keeps whatever is in it, and a document no
        # converter can read is retried for as long as it exists.
        swept = False
        for company in tenant.COMPANIES:
            owner = world.arriving(*_credentials_of(owner_of(company)))
            await owner.signs_in()
            owner.organization = await _company_named(owner, company.name)
            if owner.organization is None:
                continue
            swept = True
            # Pods first, deliberately. A pod this run made carries everything
            # the run put inside it, so removing one is worth more than any
            # number of individual deletes — and the sweep runs under a time
            # budget, so the order decides what gets done when there is not
            # enough of it.
            await _clear_run_pods(owner, ledger, mine=current().made_this)
            await _uninstall_run_connectors(owner, ledger, mine=current().made_this)
            await _only_the_cast(owner, ledger)
            standing = {pod.name for pod in tenant.STANDING_PODS}
            for pod in await owner.pods_in(owner.organization):
                if pod.get("name") in standing:
                    await _clear_run_debris(
                        owner, pod, ledger, mine=current().made_this
                    )
        if not swept:
            return "nothing to sweep: the tenant is not provisioned here"
        return ledger.report(f"Swept {current()} from {base_url}")
    finally:
        await world.aclose()


def _credentials_of(colleague: tenant.Colleague) -> tuple[str, str]:
    return colleague.label, colleague.email


async def _company_named(owner: Person, name: str) -> JSON | None:
    for organization in await owner.organizations():
        if organization.get("name") == name:
            return organization
    return None


async def _uninstall_run_connectors(
    owner: Person, ledger: Ledger, *, mine: Callable[[str], bool]
) -> None:
    """Remove the connector installations a run left in the organization.

    These are the ones that hurt most and show it least. An installation lives
    on the *organization*, so deleting the pod a scenario made does not take it
    — and every surfaces run leaves a Telegram installation behind, each with a
    connected account on the same bot token, each pointing at a stand-in on a
    port that closed when that run ended.

    One tenant reached a hundred of them, sixty Telegram. Inbound delivery then
    has sixty candidates to resolve a message against and mostly picks a dead
    one, so the agent never answers — which surfaces as four scenarios in
    `test_threads_and_files` timing out, with nothing in any log to say why.
    """
    for auth_config in await owner.auth_configs_in(owner.organization):
        name = str(auth_config.get("name", ""))
        if not mine(name):
            continue
        try:
            await owner.uninstalls_connector(
                auth_config, in_organization=owner.organization
            )
        except AssertionError:
            continue
        ledger.did(f"uninstalled {name!r} from {owner.organization.get('name')!r}")


async def _only_the_cast(owner: Person, ledger: Ledger) -> None:
    """Put the organization's membership back to the people it declares.

    Not tidiness. `test_approving_within_your_own_authority_is_allowed` proves
    that approving a join request may confer an organization role — so it does,
    every run, to somebody the run invented. Members can be removed, unlike
    organizations, so a tenant that reconciles its own membership stays the
    tenant `harness/tenant.py` describes instead of growing a stranger a night.

    Matched against the declared cast rather than against a name pattern: anyone
    who is not one of them was put there by a run, and the owner is never
    removed by construction — they are in the cast.
    """
    belongs = {colleague.email.lower() for colleague in tenant.CAST}
    for member in await owner.members_of(owner.organization):
        email = str(member.get("user_email") or member.get("email") or "").lower()
        if not email or email in belongs:
            continue
        try:
            await owner.removes_membership(member, from_organization=owner.organization)
        except AssertionError:
            continue
        ledger.did(f"removed {email} from {owner.organization.get('name')!r}")


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


async def _still_needs_a_person(owner: Person) -> str:
    """What provisioning cannot do, spelled out for whoever can.

    Gmail, GitHub and Slack are OAuth2, and the product has no way to store one
    without a browser — correctly, because consenting in a browser is what a
    real person does. So this cannot connect them, and the useful thing it can
    do is say exactly what is left and how, once, at the end.

    Reported rather than raised. A tenant with no Gmail account is perfectly
    usable for the ninety per cent of scenarios that never ask for one.
    """
    connected = {
        str(account.get("connector_id") or "")
        for account in await owner.accounts_in(owner.organization)
    }
    waiting = [action for action in consent.EVERY if action.connector not in connected]
    if not waiting:
        return "\n\nEvery third party this suite drives is connected."

    lines = [
        "",
        "",
        f"Still needs a person ({len(waiting)} of {len(consent.EVERY)}):",
        "",
    ]
    for number, action in enumerate(waiting, start=1):
        lines.append(f"  {number}. {action.name}")
        lines.append(f"     {action.how}")
        lines.append("")
    lines.append(
        "Scenarios needing these skip until they are done — they do not fail, "
    )
    lines.append("which is why this is printed rather than left to be noticed.")
    return "\n".join(lines)


def _load_authenticator(spec: str) -> Authenticator:
    """``spec`` is ``path/to/module.py:function_name``.

    A file path rather than an importable module name: the replacement is, by
    its nature, likely to live somewhere this suite's own package layout does
    not reach — a private repo entirely, in the case this was written for.
    """
    path_str, sep, func_name = spec.partition(":")
    if not sep or not func_name:
        raise SystemExit(
            f"--authenticate-with wants 'path/to/module.py:function_name', got {spec!r}"
        )
    path = Path(path_str)
    if not path.is_file():
        raise SystemExit(f"--authenticate-with: no such file {path}")
    spec_obj = importlib.util.spec_from_file_location(f"_authenticator_{path.stem}", path)
    if spec_obj is None or spec_obj.loader is None:
        raise SystemExit(f"--authenticate-with: could not load {path}")
    module = importlib.util.module_from_spec(spec_obj)
    spec_obj.loader.exec_module(module)
    try:
        return getattr(module, func_name)
    except AttributeError:
        raise SystemExit(f"--authenticate-with: {path} has no {func_name!r}") from None


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
    parser.add_argument(
        "--authenticate-with",
        default=os.getenv(AUTHENTICATE_WITH_SETTING, ""),
        help=(
            "path/to/module.py:function — get the cast signed in some other "
            f"way, for a deployment this process cannot register on by "
            f"itself (or {AUTHENTICATE_WITH_SETTING}). See this module's own "
            f"docstring."
        ),
    )
    arguments = parser.parse_args(argv)
    if not arguments.base_url:
        parser.error(
            f"no deployment given. Pass --base-url, or set {BASE_URL_SETTING}. "
            f"There is no default on purpose: this script registers accounts "
            f"and creates organizations, and an organization cannot be deleted."
        )
    authenticate = (
        _load_authenticator(arguments.authenticate_with)
        if arguments.authenticate_with
        else None
    )
    try:
        print(
            asyncio.run(
                provision(arguments.base_url, reset=arguments.reset, authenticate=authenticate)
            )
        )
    except (environment.Unreachable, AssertionError) as stopped:
        print(f"provisioning stopped: {stopped}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
