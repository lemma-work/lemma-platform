"""A release workflow must be able to name the version on every trigger it has.

These three workflows resolved the version as
``github.event.release.tag_name || inputs.version``, which covers a published
release and a hand dispatch. When the tag push was added as a trigger -- because
a release cut by a workflow raises an event GitHub will not act on, so the
release trigger fired only for a hand-cut one -- neither of those context values
exists, and both are empty. ``RELEASE_VERSION`` arrived blank and the run died on
``Invalid semver release tag:`` with nothing after the colon.

It passed review, and it passed a manual dispatch, because a dispatch supplies
``inputs.version``. The only trigger it broke was the one that had just been
added, and the only way to find out was to push a tag.

So this asserts the property rather than the spelling: for every trigger a
workflow declares, something in its version expression is non-empty. Adding a
fourth trigger without extending the expression fails here.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github/workflows"


def _load_registry():
    """`scripts/release_registry.py`, imported by path; `scripts/` is not a package."""
    path = REPO_ROOT / "scripts" / "release_registry.py"
    spec = importlib.util.spec_from_file_location("release_registry", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


registry = _load_registry()

#: One workflow per published package, read from the list the release tracker
#: and the Slack summary use -- so a package added there is held to everything
#: below without anyone remembering to add it here.
RELEASE_WORKFLOWS = tuple(package.workflow for package in registry.PACKAGES)
PACKAGES_BY_WORKFLOW = {package.workflow: package for package in registry.PACKAGES}

#: What each trigger leaves non-empty. `github.ref`/`github.ref_name` are set on
#: every event, which is the point: on a dispatch from a branch `ref_name` is
#: the *branch*, so it is populated and wrong.
POPULATED_BY = {
    "release": {"github.event.release.tag_name", "github.ref", "github.ref_name"},
    "workflow_dispatch": {"inputs.version", "github.ref", "github.ref_name"},
    "push": {"github.ref", "github.ref_name"},
}

#: Which operand must actually win on each trigger. Separate from POPULATED_BY
#: because "is non-empty" and "is the version" are different questions, and only
#: the second one is what a publish needs.
#:
#: `||` takes the first non-empty operand, so order is the whole behaviour. An
#: expression spelled `github.ref_name || inputs.version` references a populated
#: field on every trigger and would satisfy a test that only asked whether one
#: was referenced -- while publishing a package named after the branch somebody
#: dispatched from.
EXPECTED_VERSION_SOURCE = {
    "release": "github.event.release.tag_name",
    "workflow_dispatch": "inputs.version",
    "push": "github.ref_name",
}

#: The checkout resolves a git ref rather than a version string, so it ends in
#: `github.ref` (`refs/tags/v0.8.0`) where the version expressions end in
#: `github.ref_name` (`v0.8.0`).
EXPECTED_CHECKOUT_REF = (
    "github.event.release.tag_name",
    "inputs.version",
    "github.ref",
)


def _operands(expression: str) -> list:
    """The `||` alternatives, in the order GitHub evaluates them."""
    return [operand.strip() for operand in expression.split("||")]


EXPRESSION = re.compile(r"\$\{\{([^}]*)\}\}")


def _triggers(document: dict) -> set:
    # PyYAML reads the unquoted key `on:` as the boolean True.
    section = document[True] if True in document else document["on"]
    return set(section)


def _version_expressions(text: str) -> list:
    """Every expression that has to produce the release version.

    `RELEASE_VERSION` is what the publish steps read; the concurrency group is
    included because a group that collapses to a constant makes two different
    releases cancel one another.
    """
    found = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("RELEASE_VERSION:") or stripped.startswith("group:"):
            match = EXPRESSION.search(line)
            if match:
                found.append((stripped.split(":")[0], match.group(1)))
    return found


@pytest.mark.parametrize("name", RELEASE_WORKFLOWS)
def test_the_version_resolves_on_every_trigger_the_workflow_declares(name):
    path = WORKFLOWS / name
    text = path.read_text(encoding="utf-8")
    triggers = _triggers(yaml.safe_load(text))
    expressions = _version_expressions(text)
    assert expressions, "{}: found no version expression to check".format(name)

    for trigger in triggers:
        assert trigger in POPULATED_BY, (
            "{}: trigger '{}' is not described in POPULATED_BY, so this test "
            "cannot say whether the version resolves for it".format(name, trigger)
        )
        for field, expression in expressions:
            operands = _operands(expression)
            resolved = next(
                (operand for operand in operands if operand in POPULATED_BY[trigger]),
                None,
            )
            assert resolved is not None, (
                "{}: {} resolves to nothing on a '{}' event -- it reads {} and "
                "none of those is populated by that trigger.".format(
                    name, field, trigger, operands
                )
            )
            assert resolved == EXPECTED_VERSION_SOURCE[trigger], (
                "{}: {} resolves to '{}' on a '{}' event, not '{}'. `||` takes "
                "the first non-empty operand, so the order in {} is the "
                "behaviour -- this would publish the wrong string.".format(
                    name,
                    field,
                    resolved,
                    trigger,
                    EXPECTED_VERSION_SOURCE[trigger],
                    operands,
                )
            )


def _checkout_steps(document: dict) -> list:
    """Every `actions/checkout` step in the workflow, with its `with:` block."""
    found = []
    for job in (document.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            if str(step.get("uses", "")).startswith("actions/checkout"):
                found.append(step)
    return found


@pytest.mark.parametrize("name", RELEASE_WORKFLOWS)
def test_the_checkout_takes_the_triggering_ref(name):
    """The checkout must follow the release, not the default branch.

    Asserted against the checkout step itself rather than any `ref:` in the
    file: a workflow-wide search also passes for an unrelated `ref` field, and
    for a fixed value like `github.sha` that ignores `inputs.version` entirely
    -- which is the bug this is meant to exclude, not a spelling of the fix.

    Without a ref, a dispatch checks out the default branch and the version
    input merely rewrites the version string: the release's name on somebody
    else's tree, at a version number that can never be re-cut.
    """
    document = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    steps = _checkout_steps(document)
    assert steps, "{}: no actions/checkout step".format(name)

    for step in steps:
        ref = (step.get("with") or {}).get("ref")
        assert ref, (
            "{}: the checkout takes no ref, so a dispatch would publish "
            "whatever the default branch holds under the release's "
            "version".format(name)
        )
        match = EXPRESSION.search(str(ref))
        assert match, "{}: checkout ref '{}' is not an expression".format(name, ref)
        assert tuple(_operands(match.group(1))) == EXPECTED_CHECKOUT_REF, (
            "{}: checkout ref resolves {}, expected {} in that order.".format(
                name, _operands(match.group(1)), list(EXPECTED_CHECKOUT_REF)
            )
        )


@pytest.mark.parametrize("name", RELEASE_WORKFLOWS)
def test_the_tag_push_is_a_trigger(name):
    """The event a pipeline-cut release actually produces.

    A release created by a workflow is authored by github-actions[bot], and
    GitHub will not start a new workflow run from an event the default
    GITHUB_TOKEN raised -- so `release: published` alone publishes nothing. That
    is how 0.7.2's packages never reached PyPI or npm.
    """
    document = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    section = document[True] if True in document else document["on"]
    assert "push" in section, "{}: no tag-push trigger".format(name)
    assert "v*" in (section["push"] or {}).get("tags", []), (
        "{}: the push trigger does not watch version tags".format(name)
    )


def _steps(document: dict) -> list:
    jobs = list((document.get("jobs") or {}).values())
    assert len(jobs) == 1, (
        "a package release is one job: resolve, check, publish, confirm"
    )
    return jobs[0].get("steps") or []


@pytest.mark.parametrize("name", RELEASE_WORKFLOWS)
def test_the_publish_job_runs_on_every_tag_push(name):
    """No job-level condition: a tag push reaches the publish, always.

    The condition that decides whether to upload is the registry's answer,
    inside the job, where a skipped upload is a visible step rather than a job
    that silently never ran.
    """
    document = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    for job_name, job in (document.get("jobs") or {}).items():
        assert "if" not in job, "{}: job '{}' has a condition: {}".format(
            name, job_name, job["if"]
        )


@pytest.mark.parametrize("name", RELEASE_WORKFLOWS)
def test_a_release_is_idempotent_and_confirmed(name):
    """Resolve, ask the registry, do everything else only if it said no, confirm.

    Re-running a release on a version the registry already has must be a no-op
    success, and an upload is not done until the registry serves it.
    """
    package = PACKAGES_BY_WORKFLOW[name]
    steps = _steps(yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8")))
    ids = [step.get("id") for step in steps]
    assert "release" in ids and "registry" in ids, name
    resolve = steps[ids.index("release")]
    check = steps[ids.index("registry")]
    assert "release_registry.py version" in resolve["run"], name
    assert (
        "release_registry.py published {} {} ".format(package.registry, package.name)
        in check["run"]
    ), "{}: checks the wrong package or registry".format(name)
    assert ids.index("release") < ids.index("registry")

    after = steps[ids.index("registry") + 1 :]
    for step in after:
        assert step.get("if") == "steps.registry.outputs.published == 'false'", (
            "{}: step '{}' runs even when {} already has the version, so a "
            "re-run is not a no-op".format(
                name, step.get("name"), package.registry_label
            )
        )

    names = [step.get("name") for step in after]
    gate = names.index("Check version consistency across components")
    publish = names.index("Publish package")
    assert gate < publish, "{}: the version gate must run before the publish".format(
        name
    )
    assert "--expect" in after[gate]["run"]

    confirm = after[-1]
    assert (
        "release_registry.py wait {} {} ".format(package.registry, package.name)
        in confirm["run"]
    ), "{}: the last step must confirm the registry serves it".format(name)
    assert publish < len(after) - 1


def _triggers_of(name: str) -> dict:
    document = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    return document[True] if True in document else document["on"]


def test_the_release_tracker_starts_with_the_tag():
    """Progress is posted from the tag push, not after the image release ends."""
    triggers = _triggers_of("release-announce.yml")
    assert "v*" in (triggers.get("push") or {}).get("tags", [])
    assert "workflow_run" not in triggers
    text = (WORKFLOWS / "release-announce.yml").read_text(encoding="utf-8")
    assert "report_release_to_slack.py" in text
    assert "--track" in text and "--require-complete" in text


def test_the_tracker_watches_every_release_workflow():
    for workflow in registry.RELEASE_WORKFLOWS:
        assert (WORKFLOWS / workflow).is_file(), workflow
    assert set(RELEASE_WORKFLOWS) <= set(registry.RELEASE_WORKFLOWS)


def test_a_failed_tracker_on_a_tag_push_is_announced():
    """The tracker reports the others; something has to report the tracker."""
    text = (WORKFLOWS / "notify-failure.yml").read_text(encoding="utf-8")
    assert "github.event.workflow_run.name == 'Announce a release'" in text
