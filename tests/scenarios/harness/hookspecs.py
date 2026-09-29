"""What a suite built on this one may change, and where.

The platform suite proves the open-source product. A deployment built on top of
it — lemma.work's billing, say — has promises of its own, and proves them with
scenarios of its own that reuse this harness: the same `World`, the same cast,
the same stacks. Two things differ, and these hooks are exactly those two:

- **What a booted stack runs.** Another ASGI app, another interpreter, extra
  settings, a stand-in for a provider only that deployment talks to.
- **What the standing tenant needs before it can hold its pods.** A deployment
  that meters pods per owner refuses the tenant its fifth pod unless the
  organizations are on a plan that allows it — and only that deployment knows
  how to put them there.

A suite registers a plugin module the ordinary pytest way,
``pytest_plugins = ["harness.plugin", "my_suite.plugin"]``, and implements the
hooks it needs. The same module is loaded outside pytest by
``python -m harness.provision --plugin my_suite.plugin``, so provisioning a
deployment by hand runs the same preparation a test run does.

Named ``pytest_scenarios_*`` because pytest checks every ``pytest_`` hook it is
given against a spec, so a typo in an implementation fails loudly instead of
never being called.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from harness.stack import StackSpec
    from harness.world import Person, World


@pytest.hookspec
def pytest_scenarios_configure_stack(spec: StackSpec) -> None:
    """Change what a stack this suite boots will run, before it runs it.

    Called once per session, after the infrastructure is up and the backend's
    port and settings are chosen, and before migrations. Mutate ``spec`` in
    place: point ``spec.app`` and ``spec.worker`` at another application, set
    ``spec.python``/``spec.root`` to its interpreter and checkout, replace
    ``spec.migrations``, add settings to ``spec.env``, start stand-ins through
    ``spec.sidecars``, and leave anything a fixture of yours needs later in
    ``spec.extras`` (it is on ``stack.extras`` afterwards).

    ``spec.kind`` says which stack this is (``"local"`` or ``"compose"``); a
    compose stack reads ``spec.images`` and ``spec.env`` and ignores the
    process fields.
    """


@pytest.hookspec
def pytest_scenarios_prepare_tenant(
    world: World,
    people: dict[str, Person],
    organizations: dict[str, dict[str, Any]],
    base_url: str,
) -> Any:
    """Make the deployment ready to hold the standing tenant.

    Called by provisioning once the cast is signed in and both organizations
    exist with their members, and before any standing pod is made — so a
    deployment that meters pods can lift the limit first. ``people`` is keyed
    by cast label (``priya``, ``daniel``, …); ``organizations`` by company key
    (``vantage``, ``calder``), each the organization as the product reports it.

    May be a coroutine function. Must be idempotent: it runs on every
    provisioning, and on every booted stack's first use of the cast.
    """
