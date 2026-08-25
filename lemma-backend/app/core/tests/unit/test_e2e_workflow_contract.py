from __future__ import annotations

import ast
import importlib.util
import json
import pathlib
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

    Stated per *file* rather than per directory, because the layout may now
    split a directory across shards: "this directory is collected by exactly
    one shard" stopped being true of a split module while every one of its
    tests still runs exactly once. The module-level claim survives as "by at
    least one shard"; the exactly-once claim moved down to the files.
    """
    collectors = _load_planner()._collectors
    shards = _shards()
    backend = _REPO_ROOT / "lemma-backend"

    for path in (
        "app/modules/agent/tests/e2e",
        "app/modules/agent_surfaces/tests/e2e",
        "app/modules/datastore/tests/e2e",
        "app/modules/function/tests/e2e",
    ):
        files = sorted(
            str(found.relative_to(backend))
            for found in (backend / path).rglob("test_*.py")
        )
        assert files, f"{path} has no test files"
        for test_file in files:
            running = collectors(test_file, shards)
            assert len(running) == 1, f"{test_file} is run by {running or 'no shard'}"
        assert {name for f in files for name in collectors(f, shards)}, (
            f"{path} is run by no shard"
        )


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
        roots = [arg for arg in arguments if not arg.startswith("--")]
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
        # A root is a directory or a single test file -- the layout may now
        # hand one file to a different shard than its siblings. Gating on
        # `is_dir()` alone silently produced an empty source list for a
        # file-rooted shard, which read as "provisions nothing" and would have
        # let a real-sandbox shard declare it needs no image.
        sources = [
            path.read_text()
            for root in (backend / arg for arg in roots)
            for path in (root.rglob("test_*.py") if root.is_dir() else [root])
            # e2e only. A shard's args name roots like `app/core`, which also
            # contain unit tests -- including this file, which mentions the
            # marker names it is checking for and would otherwise match itself.
            if path.is_file() and "e2e" in path.parts and not _is_ignored(path)
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


def _module_marks(path: Path) -> set[str]:
    """Marker names in a module-level `pytestmark`, by AST rather than by grep.

    A substring search for "workspace" matches the word in a docstring, an
    import, or a fixture name, so it would call most of this suite
    workspace-marked. Only the module-level `pytestmark` assignment decides
    which lane a file is in, so that is the only thing read here.
    """
    marks: set[str] = set()
    for node in ast.parse(path.read_text()).body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(getattr(t, "id", "") == "pytestmark" for t in node.targets):
            continue
        for inner in ast.walk(node.value):
            if isinstance(inner, ast.Attribute):
                marks.add(inner.attr)
    return marks


def test_workspace_marked_files_are_routed_consistently_within_a_directory() -> None:
    """A split directory must not strand half its real-sandbox tests.

    `workspace` is what the sandbox lane's marker filter selects on, and every
    other shard says `not workspace`. So a file carrying that mark runs in a
    PR shard or it does not, purely by which shard collects it -- and nothing
    downstream notices the difference. Collection is not selection: a
    `workspace` file in a `not workspace` shard is collected, deselected, and
    reported as a green shard that ran none of it. `--verify` stays happy
    (the file *is* collected by exactly one shard), and
    `needs_sandbox_images` stays correct (the shard provisions nothing,
    because nothing ran).

    That is the failure mode of splitting a file across shards, which the
    layout can now do. Deliberately deselected files exist and are fine --
    `agent`'s two workspace files run in the protected lane on purpose, as do
    pod_bundle's and workflow's -- so the invariant is not "always selected".
    It is that a directory's workspace files agree with each other: whatever
    lane they were in before a split, they are all still in it after.
    """
    collectors = _load_planner()._collectors
    shards = _shards()
    by_name = {shard["name"]: shard for shard in shards}
    backend = _REPO_ROOT / "lemma-backend"

    def _selected(test_file: str) -> bool:
        return any(
            "not workspace" not in by_name[name]["markers"]
            for name in collectors(test_file, shards)
        )

    routing: dict[Path, dict[str, bool]] = {}
    for path in sorted(backend.glob("app/**/tests/e2e/**/test_*.py")):
        if "workspace" not in _module_marks(path):
            continue
        relative = str(path.relative_to(backend))
        routing.setdefault(path.parent, {})[relative] = _selected(relative)

    assert routing, "no workspace-marked e2e files found; the AST walk is wrong"

    for directory, files in routing.items():
        assert len(set(files.values())) == 1, (
            f"{directory.relative_to(backend)} routes its workspace-marked "
            f"files to shards that disagree about the `workspace` marker: "
            f"{ {name: 'runs' for name, ran in files.items() if ran} } vs "
            f"{ {name: 'deselected' for name, ran in files.items() if not ran} }"
            ". Split files keep the lane their siblings are in."
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

    planner = _load_planner()

    def _modules_run_by(shard: dict) -> set[str]:
        """Which modules this shard actually collects, not which its args name.

        The catch-all's args are `app/modules app/core` plus ignores, so reading
        module names off the args sees "core" and nothing else -- it missed
        every module the catch-all sweeps up by default, which is most of them.
        """
        return {
            planner._module_of(str(path.relative_to(backend)))
            for path in backend.glob("app/**/tests/e2e/**/test_*.py")
            if planner._collectors(str(path.relative_to(backend)), [shard])
        }

    for shard in _shards():
        # Asked of the planner rather than reimplemented here. This test used to
        # carry its own copy that matched `async def worker`, which finds
        # `agent_surfaces` and misses `datastore` -- whose session-scoped
        # fixture is named `document_worker` and runs the same
        # `production_worker_process`. The copy said a datastore+pod_bundle
        # shard was fine; four `test_connector_import_e2e` tests then hung for
        # the full 120-second cap, with the worker alive and simply not being
        # delivered anything.
        owners, base_users = planner._session_worker_conflict(backend, shard["markers"])
        modules = _modules_run_by(shard)
        own = modules & owners
        base = modules & base_users
        assert not (own and base), (
            f"shard {shard['name']!r} packs {sorted(own)} (starts its own "
            f"session worker) with {sorted(base)} (uses the base session "
            f"worker); `production_worker_process` flushes the shared Redis "
            f"database on entry and on teardown, which drops the base worker's "
            f"consumer group"
        )
