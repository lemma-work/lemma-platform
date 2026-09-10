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

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github/workflows"

RELEASE_WORKFLOWS = (
    "release-lemma-python.yml",
    "release-lemma-terminal.yml",
    "release-lemma-typescript.yml",
)

#: What each trigger leaves populated. `github.ref`/`github.ref_name` are set on
#: every event, but only name the version on a tag push -- on a dispatch from a
#: branch `ref_name` is the branch, which is why `inputs.version` has to win.
POPULATED_BY = {
    "release": {"github.event.release.tag_name", "github.ref", "github.ref_name"},
    "workflow_dispatch": {"inputs.version"},
    "push": {"github.ref", "github.ref_name"},
}

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
            referenced = set(re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", expression))
            assert referenced & POPULATED_BY[trigger], (
                "{}: {} resolves to nothing on a '{}' event -- it reads {} and "
                "none of those is populated by that trigger.".format(
                    name, field, trigger, sorted(referenced)
                )
            )


@pytest.mark.parametrize("name", RELEASE_WORKFLOWS)
def test_the_checkout_pins_a_ref(name):
    """Without one, a dispatch builds the default branch under a release's name."""
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    assert re.search(r"^\s+ref:\s*\$\{\{", text, re.MULTILINE), (
        "{}: the checkout takes no ref, so a workflow_dispatch would publish "
        "whatever the default branch holds under the release's version".format(name)
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
