"""Reading which lane a scenario's proof actually runs in.

`covered` is decided from source, never from a run — so a promise whose only
proving test carries `sandbox`, `live` or `stack_lane` reports exactly the same
as one demonstrated on every push, while nothing routinely collects it. These
tests cover the marker reading that lets the report say which is which.

The lane marks are written three different ways across the suite, and all three
have to be read, so each is pinned here.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from check_scenario_coverage import (  # noqa: E402
    DESELECTED_LANES,
    _lane_marks,
    _module_lane_marks,
)


def _decorators(source: str) -> list[ast.AST]:
    tree = ast.parse(source)
    return list(tree.body[0].decorator_list)


def test_a_qualified_pytest_mark_is_read() -> None:
    """`@pytest.mark.live`, the form the live journeys use."""
    assert _lane_marks(_decorators("@pytest.mark.live\ndef test_x(): ...")) == {"live"}


def test_a_called_lane_marker_is_read() -> None:
    """`@stack_lane("why")` — a helper that takes its reason as an argument."""
    assert _lane_marks(
        _decorators('@stack_lane("no spend limit here")\ndef test_x(): ...')
    ) == {"stack_lane"}


def test_an_ordinary_decorator_is_not_a_lane() -> None:
    """`@proves(...)` and friends must not be mistaken for a lane mark."""
    assert (
        _lane_marks(
            _decorators('@proves("PS-POD-051")\n@covers("pod.list")\ndef test_x(): ...')
        )
        == set()
    )


def test_a_module_level_pytestmark_applies_to_the_file() -> None:
    """The live journeys mark the whole module, not each test.

    Read from the assignment rather than from any test's decorators, because
    there are none to read — miss this and a whole directory reports as though
    the default lane ran it.
    """
    tree = ast.parse(
        "pytestmark = [journey('Live'), capability('x'), pytest.mark.live]\n"
    )
    assert _module_lane_marks(tree) == {"live"}


def test_a_module_level_mark_that_is_not_a_list_is_read_too() -> None:
    """`pytestmark = pytest.mark.sandbox` is legal and means the same thing."""
    assert _module_lane_marks(ast.parse("pytestmark = pytest.mark.sandbox\n")) == {
        "sandbox"
    }


def test_a_module_with_no_pytestmark_claims_no_lane() -> None:
    assert _module_lane_marks(ast.parse("x = 1\n")) == set()


def test_the_lane_names_match_what_the_suite_actually_deselects() -> None:
    """Guard against the list drifting from the suite's own configuration.

    `sandbox` and `live` come out of the scenario suite's default `-m`
    expression; `stack_lane` is deselected in its conftest under `--base-url`.
    If one is renamed there and not here, this report silently starts calling
    an uncollected promise plainly covered again.
    """
    suite = REPO_ROOT / "tests" / "scenarios"
    selection = (suite / "pyproject.toml").read_text(encoding="utf-8")
    assert "not sandbox and not live" in selection
    assert '"stack_lane"' in (suite / "conftest.py").read_text(encoding="utf-8")
    assert set(DESELECTED_LANES) == {"sandbox", "live", "stack_lane"}
