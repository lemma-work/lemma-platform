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

    Overlay shards are excluded from the count, not from the layout. An overlay
    deliberately collects directories the packed lanes also hold and separates
    from them by marker -- `indexing` and `not indexing` cannot both select the
    same test -- so counting it here would report every datastore and pod file
    as doubly-run when nothing runs twice. That every test really is selected
    once is a stronger claim than this one and is checked where it can be:
    `plan_e2e_shards.py --verify` collects the suite and asks each lane's
    filter, which is the only way to see a marker, and therefore the only way
    to see an overlay.
    """
    collectors = _load_planner()._collectors
    shards = [shard for shard in _shards() if not shard.get("overlay")]
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
    assert "--lane e2e_union" in workflow
    assert workflow.index("Combine E2E-only coverage") > workflow.index(
        "Download validated unit coverage"
    )


def test_every_module_of_any_size_has_a_recorded_coverage_floor() -> None:
    """Four modules were named on the command line and the rest had none.

    A module with no floor can lose all of its coverage while the whole-repo
    figure -- one number over ~190k statements -- barely moves, which is how
    the largest modules came to be the least protected. The floors are a
    ratchet now, so this asserts every module the gate measures is in it: a new
    module arriving without one is the case that would otherwise pass quietly.
    """
    baseline = json.loads(
        (_REPO_ROOT / "lemma-backend/coverage-baseline.json").read_text()
    )

    assert set(baseline) == {"combined", "e2e_union"}, sorted(baseline)
    for lane, floors in baseline.items():
        assert floors, f"{lane} records no floors at all"
        for module, floor in floors.items():
            assert 0 < float(floor) <= 100, f"{lane}/{module} floor is {floor}"

    # `test_support` is scaffolding for other modules' tests and is not
    # measured at all; `analytics` is five statements, where one of them is
    # worth twenty points and a floor would report noise rather than coverage.
    # Anything else arriving without a floor is the case this test is for.
    unfloored = {"test_support", "analytics"}
    modules = {
        path.name
        for path in (_REPO_ROOT / "lemma-backend/app/modules").iterdir()
        if path.is_dir() and not path.name.startswith("_")
    } - unfloored
    for lane, floors in baseline.items():
        missing = sorted(modules - set(floors))
        assert not missing, (
            f"{lane} has no coverage floor for {missing}. Run "
            f"`make coverage-baseline` after a full coverage run — a module "
            f"with no floor is one nothing would notice going uncovered."
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


def test_a_shard_can_be_reproduced_locally_without_copying_its_markers() -> None:
    """`make test-e2e-shard` must not carry its own copy of a marker filter.

    Two of the seven shards run a different filter from the rest: `sandbox` and
    `sandbox-2` keep `workspace` tests in, because function and workspace
    execution against real Docker are what those shards exist to prove. The
    target used to hard-code the *other* filter -- the one that excludes
    `workspace` -- and ignore the `E2E_SHARD_MARKERS` variable it appeared to
    accept.

    Measured on the `sandbox` shard's own paths: CI selects 28 tests, the
    hard-coded filter selected 6. So `make test-e2e-shard E2E_ARGS=<shard args>`
    ran a fifth of the shard, went green, and the shard failed on the pull
    request anyway. A local reproduction that quietly runs less than CI is worse
    than none, because it is trusted.
    """
    makefile = (_REPO_ROOT / "lemma-backend/Makefile").read_text()
    body = makefile.split("\ntest-e2e-shard:\n", 1)[1].split("\n\n", 1)[0]

    assert "$(E2E_SHARD_MARKERS)" in body, (
        "test-e2e-shard must take its marker filter from E2E_SHARD_MARKERS so a "
        "caller can pass a shard's own; a literal here cannot express the two "
        "filters the shards actually use."
    )
    assert "not workspace" not in body, (
        "a literal marker filter is back in test-e2e-shard: it would deselect "
        "every Docker-sandbox test while claiming to reproduce a shard."
    )


def test_every_shard_name_resolves_for_the_local_runner() -> None:
    """`make test-e2e-shard-ci SHARD=<name>` must work for every shard.

    The runner reads `.github/e2e-shards.json`, so a regenerated layout that
    renames or adds a shard stays reproducible without anyone editing a target.
    """
    runner = _REPO_ROOT / "scripts/run_e2e_shard.py"
    assert runner.exists(), "the local shard runner is gone"

    source = runner.read_text()
    assert '.github" / "e2e-shards.json"' in source, (
        "the runner must read the same shard file the workflow does"
    )

    names = {str(shard["name"]) for shard in _shards()}
    assert names, "no shards defined"
    # Every shard carries the three things the runner needs. A shard missing one
    # would fail only when somebody tried to reproduce it, which is the moment
    # they can least afford it.
    for shard in _shards():
        assert shard.get("args"), f"{shard['name']} has no args"
        assert shard.get("markers"), f"{shard['name']} has no markers"
        assert shard.get("workers"), f"{shard['name']} has no worker count"


def test_the_protected_lane_can_be_reproduced_from_the_makefile() -> None:
    """`make test-e2e-runtime` must select exactly what the protected job does.

    The two had drifted in three ways at once: the target selected `provider`
    where the workflow says `not provider`, carried a marker the workflow did
    not, and left `E2E_LLM_MODE` unset. So the one command a developer would
    reach for to reproduce the protected lane failed on tests CI never runs and
    covered none it does.

    That lane is worth being able to run. Its own workflow comment records that
    it silently did nothing for eleven days -- `--extra markitdown` had stopped
    existing -- and blocked every Desktop release while it did.

    Two clauses have since come out of the filter, both because they selected
    nothing. `not mock_sandbox_only` named two workflow tests that were excluded
    here in #155 and given no other lane, so they ran nowhere for eight weeks --
    and they pass in this lane's exact configuration, so the exclusion outlived
    whatever it was for. `or surface_live` was inert by construction: that file
    is `provider` at module level and this filter says `not provider`, so the
    clause read like a lane and was one for nobody. Its `LEMMA_RUN_SURFACE_LIVE_E2E`
    went with it; the smoke job in `e2e.yml` still sets it, which is where those
    tests actually run.
    """
    makefile = (_REPO_ROOT / "lemma-backend/Makefile").read_text()
    workflow = (_REPO_ROOT / ".github/workflows/backend-protected-e2e.yml").read_text()

    target = makefile.split("\ntest-e2e-runtime:\n", 1)[1].split("\n\n", 1)[0]

    marker = (
        "e2e and (slow or workspace or indexing or local_cli "
        "or protected) and not provider"
    )
    assert marker in workflow, (
        "the protected workflow's marker filter changed; update this test and "
        "the make target together, which is the point of it being here"
    )
    assert marker in target, (
        "make test-e2e-runtime no longer selects what the protected job does"
    )
    # The environment is part of the selection: `E2E_LLM_MODE=mock` is what
    # keeps this lane real about Docker without needing a paid model key.
    for setting in ("E2E_REAL=1", "E2E_LLM_MODE=mock"):
        assert setting in target, f"{setting} missing from the target"


def test_the_two_non_shard_lanes_match_their_workflows() -> None:
    """`--verify` decides coverage, so its copy of a lane must be the lane.

    The shard lanes need no such check: the gate reads their filters straight
    out of `.github/e2e-shards.json`, which is the file the workflow runs from.
    The other two lanes are not in that file, so the planner holds a copy of
    each -- and a stale copy would not fail loudly. It would report a test as
    covered by a lane whose filter no longer selects it, which is the exact
    shape of the bug the gate exists to catch, wearing the gate's own colours.
    """
    planner = _load_planner()
    protected = (_REPO_ROOT / ".github/workflows/backend-protected-e2e.yml").read_text()
    e2e = (_REPO_ROOT / ".github/workflows/e2e.yml").read_text()

    assert planner.PROTECTED_MARKERS in protected, (
        "plan_e2e_shards.PROTECTED_MARKERS no longer matches the protected "
        "workflow; --verify would credit that lane with tests it deselects"
    )
    assert planner.SMOKE_PATH in e2e, (
        "the surface-live smoke job no longer runs plan_e2e_shards.SMOKE_PATH"
    )
    assert f"-m {planner.SMOKE_MARKERS}" in e2e, (
        "the surface-live smoke job's marker filter changed"
    )


#: Markers declared in `pytest.ini` that no test carries, and why that is
#: deliberate. Both are named in CI marker filters, so they read as load-bearing
#: and are not: `not protected` in every shard excludes nothing, and
#: `or protected` in the protected lane selects nothing. They stay declared
#: because `--strict-markers` is on, so the day someone marks a test with one it
#: must already exist -- and because a lane named after a marker should keep the
#: name available. `identity` and `pod` had no such excuse and are gone.
RESERVED_UNUSED_MARKERS = {
    "protected": "reserved for backend-protected-e2e.yml; nothing carries it yet",
    "local_cli": "reserved for tests needing a local Codex/OpenCode/Claude binary",
}


def test_no_marker_is_declared_and_forgotten() -> None:
    """Every declared marker is used, or is listed as deliberately reserved.

    A marker nobody carries still changes how a filter reads. `not protected`
    looks like it removes something and removes nothing; `or protected` looks
    like it adds a lane's worth of tests and adds none. Someone reading the
    protected lane's filter would reasonably believe it covers more than it
    does.

    This does not fail on a reserved marker -- it fails when the reserved list
    and reality disagree in either direction, so a marker that starts being used
    gets taken off the list, and a marker that quietly dies gets noticed.
    """
    import re

    lines = (_REPO_ROOT / "lemma-backend/pytest.ini").read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("markers"))
    declared: set[str] = set()
    for line in lines[start + 1 :]:
        if line.strip() and not line.startswith((" ", "\t")):
            break
        if ":" in line:
            declared.add(line.split(":", 1)[0].strip())

    used: set[str] = set()
    for pattern in ("test_*.py", "conftest.py"):
        for path in (_REPO_ROOT / "lemma-backend").rglob(pattern):
            if ".venv" in str(path):
                continue
            source = path.read_text(errors="ignore")
            used.update(re.findall(r"pytest\.mark\.(\w+)", source))
            # Markers the collection hook attaches by path or fixture, which no
            # decorator spells out.
            used.update(re.findall(r"add_marker\(pytest\.mark\.(\w+)", source))

    unused = declared - used
    assert unused == set(RESERVED_UNUSED_MARKERS), (
        f"declared markers nobody uses: {sorted(unused - set(RESERVED_UNUSED_MARKERS))}; "
        f"reserved markers now in use: {sorted(set(RESERVED_UNUSED_MARKERS) - unused)}. "
        "Use it, delete it, or record why it is reserved."
    )


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
