from __future__ import annotations

import importlib.util
import json
import pathlib
import re
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


def test_every_shard_that_provisions_a_sandbox_declares_the_image() -> None:
    """`needs_sandbox_images` must match which shards actually need one.

    Not "only the sandbox shard": the agent module's `fast_workspace` journeys
    provision a real Docker workspace too, and conftest exempts them from the
    `workspace` marker precisely so they stay in the fast lane. When that shard
    did not declare the image it still got one -- the `workspace_image` fixture
    built it on demand, inside the test step, uncached, and invisibly, because
    pytest captures the build output.

    So the invariant is derived from the tests rather than pinned to a name: a
    shard needs the image exactly when its directories contain a test that asks
    for one.
    """
    backend = _REPO_ROOT / "lemma-backend"
    provisioning_markers = ("fast_workspace", "configure_workspace_api_url")

    for shard in _shards():
        arguments = shard["args"].split()
        directories = [arg for arg in arguments if not arg.startswith("--")]
        # The catch-all shard collects whole roots and subtracts with --ignore,
        # so the ignored subtrees are not its tests and must not count.
        ignored = [
            arg.split("=", 1)[1] for arg in arguments if arg.startswith("--ignore=")
        ]

        def _is_ignored(path: pathlib.Path) -> bool:
            relative = path.relative_to(backend).as_posix()
            return any(
                relative == entry or relative.startswith(entry + "/")
                for entry in ignored
            )

        # Asking for the workspace fixtures is not enough to need the image in
        # *this* shard. conftest auto-marks such a test `workspace` unless it
        # carries `fast_workspace`, and a shard whose filter says "not
        # workspace" deselects it -- which is exactly how pod_bundle and
        # workflow reference these fixtures while never provisioning anything.
        excludes_workspace = "not workspace" in shard["markers"]
        sources = [
            path.read_text()
            for directory in directories
            if (backend / directory).is_dir()
            for path in (backend / directory).rglob("test_*.py")
            # e2e only. A shard's args name roots like `app/core`, which also
            # contain unit tests -- including this file, which mentions the
            # marker names it is checking for and would otherwise match itself.
            if "e2e" in path.parts and not _is_ignored(path)
        ]
        if excludes_workspace:
            provisions = any("fast_workspace" in text for text in sources)
        else:
            provisions = any(
                any(marker in text for marker in provisioning_markers)
                for text in sources
            )
        assert shard.get("needs_sandbox_images", False) == provisions, (
            f"shard {shard['name']!r} declares "
            f"needs_sandbox_images={shard.get('needs_sandbox_images', False)} "
            f"but its tests provision a sandbox: {provisions}"
        )


def test_e2e_union_gate_is_separate_from_unit_aggregate() -> None:
    workflow = (_REPO_ROOT / ".github/workflows/backend-coverage.yml").read_text()

    assert "coverage-backend/e2e-union.json" in workflow
    assert "--min-module agent=80" in workflow
    # 79, not 80, and pinned here so moving it stays a deliberate act. The
    # floor was set fractionally above the value it measures: across 38 runs
    # where this gate executed, `agent_surfaces` reported 79.56 to 79.98 and
    # failed 32 of them, with four head SHAs producing both a pass and a
    # failure. That is xdist coverage variance -- the same cause
    # `.github/e2e-shards.json` records a ~0.4-point spread for on `agent` --
    # not a number anybody can push over the line. Raise it when real coverage
    # moves.
    assert "--min-module agent_surfaces=79" in workflow
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


def test_a_shard_never_runs_two_session_workers_in_one_process() -> None:
    """Two streaq workers in one pytest process fight over one Redis queue.

    `agent_surfaces` overrides the session-scoped `worker` fixture with its own
    (its Composio transport differs), which is legitimate. What is not safe is
    both it and the base `worker` being instantiated in the same process: they
    share the per-xdist-worker Redis database, so they consume from the same
    streaq queue, and `production_worker_process` calls `flushdb` on entry --
    so the second one to start wipes the first one's queue.

    Today's layout avoids this by luck: `agent_surfaces` is packed with
    `identity`, which asks for no worker at all. But the layout is generated by
    an LPT packer, so a regeneration could pair it with `agent`, `pod_bundle`,
    `schedule`, `datastore` or `workflow` and turn a green suite intermittently
    red for reasons that would look nothing like the cause. This fails the
    build at review time instead.
    """
    backend = _REPO_ROOT / "lemma-backend"

    def _modules(shard: dict) -> set[str]:
        return {
            part.split("/")[2]
            for part in shard["args"].split()
            if part.startswith("app/modules/")
        }

    overrides_worker = {
        path.parts[-4]
        for path in backend.glob("app/modules/*/tests/e2e/conftest.py")
        if "async def worker" in path.read_text()
    }
    # Requesting it as a fixture, not merely mentioning the word: the string
    # "worker" appears in comments and docstrings all over this suite, and
    # matching those put `identity` -- which asks for no worker at all -- on
    # this list.
    requests_worker = re.compile(r"(?m)^\s+worker[,:)]|pytest\.mark\.worker")
    uses_base_worker = {
        path.parts[-4]
        for path in backend.glob("app/modules/*/tests/e2e/test_*.py")
        if requests_worker.search(path.read_text())
    } - overrides_worker

    for shard in _shards():
        modules = _modules(shard)
        own = modules & overrides_worker
        base = modules & uses_base_worker
        assert not (own and base), (
            f"shard {shard['name']!r} packs {sorted(own)} (own session worker) "
            f"with {sorted(base)} (base session worker); two streaq workers in "
            f"one process flush each other's queue"
        )
