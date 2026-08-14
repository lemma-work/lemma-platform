"""The wheel must be installable by someone who does not have this repository.

lemma-terminal depends on two things that live outside its own directory: the
agent skills, and ``lemma_pod_bundle``. Neither is published to PyPI, so both
have to travel inside the wheel. The release workflow used to arrange that by
rewriting ``pyproject.toml`` with regexes at publish time, which meant the only
build anyone ever verified was the one nobody could reproduce locally — and a
mistake in it would surface as ``pip install lemma-terminal`` failing for
users, after the release.

``setup.py`` now does the vendoring for every build. These tests hold the parts
of that contract that can be checked without building: a wheel-level check would
be a much slower test of the same properties, and the release workflow keeps one.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_CLI_ROOT = Path(__file__).resolve().parents[1]
_PYPROJECT = tomllib.loads((_CLI_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

#: Distributions that exist only inside this monorepo. A runtime dependency on
#: any of them would make every install from PyPI fail on a missing package.
UNPUBLISHED_DISTRIBUTIONS = ("lemma-pod-bundle",)


def _runtime_requirements() -> list[str]:
    return list(_PYPROJECT["project"]["dependencies"])


def test_no_unpublished_distribution_is_a_runtime_dependency() -> None:
    """These reach users through vendoring, never through Requires-Dist."""
    leaked = [
        requirement
        for requirement in _runtime_requirements()
        for name in UNPUBLISHED_DISTRIBUTIONS
        if requirement.replace("_", "-").lower().startswith(name)
    ]
    assert not leaked, (
        f"{leaked} is not on PyPI, so declaring it here makes every "
        "`pip install lemma-terminal` fail. Vendor it in setup.py and declare "
        "it in [dependency-groups] dev instead."
    )


def test_the_pod_bundle_source_is_still_available_to_developers() -> None:
    """Dropping it from runtime deps must not drop it from the dev environment.

    The CLI imports ``lemma_pod_bundle`` at runtime, so the test suite and local
    runs need it resolvable even though the wheel carries its own copy.
    """
    dev_group = " ".join(_PYPROJECT["dependency-groups"]["dev"])
    assert "lemma-pod-bundle" in dev_group
    assert "lemma-pod-bundle" in _PYPROJECT["tool"]["uv"]["sources"]


def test_the_vendored_package_is_included_in_the_wheel() -> None:
    """setup.py copies it in; packages.find is what actually ships it."""
    include = _PYPROJECT["tool"]["setuptools"]["packages"]["find"]["include"]
    assert any(pattern.startswith("lemma_pod_bundle") for pattern in include), include
    assert any(pattern.startswith("lemma_cli") for pattern in include), include


def test_the_build_vendors_both_sources() -> None:
    """A build hook that stopped running would publish an importable-looking gap.

    Asserted against setup.py's text rather than by running a build: the point is
    that both vendoring steps are wired into the build commands, which is a
    structural property, and a real build lives in the release workflow.
    """
    setup_py = (_CLI_ROOT / "setup.py").read_text(encoding="utf-8")
    assert "_vendor_skills()" in setup_py
    assert "_vendor_pod_bundle()" in setup_py
    for command in ("build_py", "sdist"):
        assert command in setup_py, f"{command} must vendor before it packages"


def test_vendoring_runs_before_setup_is_called() -> None:
    """``packages.find`` runs at configuration time, before any build command.

    A copy made only from ``build_py`` lands after the package list is already
    fixed, so ``lemma_pod_bundle`` exists on disk but ships in no wheel — and
    because the copy survives in the tree, the next build in that same tree
    quietly succeeds. Only a build from a fresh checkout shows it, which is
    exactly the build users get from a git install.
    """
    setup_py = (_CLI_ROOT / "setup.py").read_text(encoding="utf-8")
    vendor_at_import = setup_py.index("\n_vendor_all()\n")
    setup_call = setup_py.index("\nsetup(")
    assert vendor_at_import < setup_call, (
        "setup.py must call _vendor_all() at module level before setup(), or "
        "packages.find cannot discover the vendored package."
    )
