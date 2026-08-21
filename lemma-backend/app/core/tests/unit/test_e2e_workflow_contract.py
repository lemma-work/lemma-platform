from __future__ import annotations

import importlib.util
import json
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[5]


def _load_planner():
    """The shipped shard planner, imported rather than reimplemented.

    An earlier version of the test below reimplemented pytest's collect/ignore
    rules and got them wrong, which is the failure mode a contract test is
    supposed to prevent, not demonstrate.
    """
    spec = importlib.util.spec_from_file_location(
        "plan_e2e_shards", _REPO_ROOT / "scripts/plan_e2e_shards.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _shards() -> list[dict]:
    """The shard layout, read from where it is now generated.

    It used to be hand-written in e2e.yml, so these contracts matched on
    `- name: <shard>` in the workflow text. It is now produced by
    scripts/plan_e2e_shards.py from measured JUnit and committed as JSON, so
    the same intent is checked against the same facts in their new home.
    """
    return json.loads((_REPO_ROOT / ".github/e2e-shards.json").read_text())["shards"]


def test_every_critical_module_is_covered_by_exactly_one_e2e_shard() -> None:
    """The four modules with their own coverage floors must each be run.

    Names are deliberately not pinned any more -- the packer is free to
    rebalance and rename shards -- but a critical module silently falling out
    of the matrix is the failure this has always existed to catch.
    """
    collectors = _load_planner()._collectors
    shards = _shards()

    for path in (
        "app/modules/agent/tests/e2e",
        "app/modules/agent_surfaces/tests/e2e",
        "app/modules/datastore/tests/e2e",
        "app/modules/function/tests/e2e",
    ):
        running = collectors(path, shards)
        assert len(running) == 1, f"{path} is run by {running or 'no shard'}"


def test_sandbox_shard_is_the_only_one_that_builds_docker_images() -> None:
    """Building the sandbox images twice raced one cache key and paid twice."""
    building = [s["name"] for s in _shards() if s.get("needs_sandbox_images")]
    assert building == ["sandbox"], building


def test_e2e_union_gate_is_separate_from_unit_aggregate() -> None:
    workflow = (_REPO_ROOT / ".github/workflows/backend-coverage.yml").read_text()

    assert "coverage-backend/e2e-union.json" in workflow
    assert "--min-module agent=80" in workflow
    assert "--min-module agent_surfaces=80" in workflow
    assert "--min-module datastore=80" in workflow
    assert "--min-module function=80" in workflow
    assert workflow.index("Combine E2E-only coverage") > workflow.index(
        "Download validated unit coverage"
    )


def test_coverage_aggregation_is_not_on_the_pull_request_critical_path() -> None:
    """It gates nothing and cost 256s at the end of every PR, so it moved.

    Off the e2e workflow entirely, onto a workflow_run consumer -- which is
    also the only trigger that can reach the unit-coverage artifact from the
    "CI" run rather than regenerating it by re-running the whole unit suite.
    """
    e2e_workflow = (_REPO_ROOT / ".github/workflows/e2e.yml").read_text()
    coverage_workflow = (
        _REPO_ROOT / ".github/workflows/backend-coverage.yml"
    ).read_text()

    assert "aggregate-coverage" not in e2e_workflow
    assert "check_coverage_thresholds" not in e2e_workflow
    assert "diff-cover" not in e2e_workflow
    assert 'workflows: ["Backend E2E"]' in coverage_workflow
    # The expensive fallback is gone: it re-ran the entire unit suite because
    # the workflow_run fast path above it was unreachable.
    assert "make coverage-backend-unit" not in coverage_workflow


def test_ci_publishes_one_authoritative_module_wise_coverage_comment() -> None:
    coverage_workflow = (
        _REPO_ROOT / ".github/workflows/backend-coverage.yml"
    ).read_text()
    e2e_workflow = (_REPO_ROOT / ".github/workflows/e2e.yml").read_text()
    ci_workflow = (_REPO_ROOT / ".github/workflows/ci.yml").read_text()

    assert "Publish one authoritative PR coverage comment" in coverage_workflow
    assert "<!-- lemma-backend-coverage:overall -->" in coverage_workflow
    assert "--unit-coverage-json" in coverage_workflow
    assert "--e2e-coverage-json" in coverage_workflow
    assert "--combined-coverage-json" in coverage_workflow
    assert "Publish authoritative E2E union PR comment" not in coverage_workflow
    assert "Update PR backend coverage comment" not in coverage_workflow
    assert "Update PR backend coverage comment" not in ci_workflow
    # The invariant is one comment, not one action. Counting
    # `actions/github-script` used to stand in for that and no longer can: the
    # workflow now also uses it to locate the CI run that produced the unit
    # coverage artifact, which posts nothing.
    assert coverage_workflow.count("createComment") == 1
    assert coverage_workflow.count("updateComment") == 1
    # And the e2e workflow must not grow a second author of its own.
    assert "github-script" not in e2e_workflow


def test_required_e2e_allows_hermetic_worker_scenarios() -> None:
    makefile = (_REPO_ROOT / "lemma-backend/Makefile").read_text()

    fast_filter = next(
        line for line in makefile.splitlines() if "e2e and not slow" in line
    )
    assert "not worker" not in fast_filter
    assert "not workspace" in fast_filter
    assert "not provider" in fast_filter
    assert "not protected" in fast_filter

    conftest = (_REPO_ROOT / "lemma-backend/conftest.py").read_text()
    workspace_fixtures = conftest.split("WORKSPACE_FIXTURES =", 1)[1].split("}", 1)[0]
    assert '"backend_server"' not in workspace_fixtures
