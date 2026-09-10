"""Contracts that keep dependency automation and CI runner usage bounded."""

from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[5]


def _read(path: str) -> str:
    return (_REPO_ROOT / path).read_text()


def test_native_desktop_dependency_caches_are_shared_across_build_workflows() -> None:
    import yaml

    native_caches = set()
    for path in (
        ".github/workflows/ci.yml",
        ".github/workflows/release-desktop.yml",
        ".github/workflows/release-local-images.yml",
    ):
        workflow = yaml.safe_load(_read(path))
        for name, job in workflow["jobs"].items():
            for index, step in enumerate(job.get("steps", [])):
                settings = step.get("with", {})
                if settings.get("prefix-key") != "desktop-native":
                    continue
                assert step["uses"] == "Swatinem/rust-cache@v2"
                assert settings["workspaces"] == "desktop"
                assert settings["shared-key"] == "${{ runner.os }}-${{ runner.arch }}"
                assert "save-if" not in settings, "PRs need merge-ref cache reuse"
                assert not settings.get("cache-all-crates", False)
                assert not settings.get("cache-workspace-crates", False)
                assert any(
                    previous.get("uses", "").startswith("dtolnay/rust-toolchain@")
                    for previous in job["steps"][:index]
                ), "the cache must key the selected compiler, not the runner default"
                native_caches.add((path, name))
    assert native_caches == {
        (".github/workflows/ci.yml", "desktop"),
        (".github/workflows/ci.yml", "desktop-windows"),
        (".github/workflows/release-desktop.yml", "build-dmg"),
        (".github/workflows/release-desktop.yml", "build-windows"),
        (".github/workflows/release-local-images.yml", "share-desktop-dmg"),
        (".github/workflows/release-local-images.yml", "share-desktop-exe"),
    }


def test_desktop_npm_cache_tracks_settings_and_cli_dependencies() -> None:
    import yaml

    workflow = yaml.safe_load(_read(".github/workflows/ci.yml"))
    for name in ("desktop", "desktop-windows"):
        node = next(
            step
            for step in workflow["jobs"][name]["steps"]
            if step.get("uses", "").startswith("actions/setup-node@")
        )
        assert node["with"]["cache"] == "npm"
        inputs = node["with"]["cache-dependency-path"].splitlines()
        assert "desktop/ui-tests/package-lock.json" in inputs
        assert "desktop/scripts/tauri-cli-version.txt" in inputs


def test_dependabot_is_monthly_grouped_and_uv_native() -> None:
    """Every ecosystem is monthly, grouped, and has somewhere to put a fix.

    Asserted per ecosystem rather than by counting occurrences. The counts said
    "four of these strings appear" -- true of a config where one ecosystem had
    both and another had neither, and false of a correct config the moment a
    fifth ecosystem was added. Adding cargo coverage is what surfaced that.
    """
    import yaml

    config = _read(".github/dependabot.yml")
    assert "package-ecosystem: pip" not in config, "uv is the native ecosystem"

    updates = yaml.safe_load(config)["updates"]
    ecosystems = {entry["package-ecosystem"] for entry in updates}
    # `cargo` is here because `desktop` is a cargo workspace: without an entry a
    # Rust advisory has nothing to open a pull request against.
    assert {"uv", "npm", "github-actions", "docker", "cargo"} <= ecosystems

    for entry in updates:
        name = entry["package-ecosystem"]
        assert entry["schedule"]["interval"] == "monthly", name
        applies = {group["applies-to"] for group in entry["groups"].values()}
        assert applies == {"version-updates", "security-updates"}, name
        # Two groups need two slots. At one, an open routine pull request holds
        # the only one and security updates queue behind it indefinitely.
        assert entry["open-pull-requests-limit"] >= len(entry["groups"]), name

    for directory in (
        "/lemma-cli",
        "/lemma-pod-bundle",
        "/lemma-python",
        "/lemma-stack",
    ):
        assert f"- {directory}" in config


def test_backend_changes_do_not_trigger_committed_spec_codegen() -> None:
    workflow = _read(".github/workflows/ci.yml")
    codegen_filter = workflow.split("            codegen:\n", 1)[1].split(
        "\n\n  backend-unit:", 1
    )[0]

    assert "lemma-python/lemma_sdk/openapi_spec.json" in codegen_filter
    assert "lemma-backend/app/**" not in codegen_filter
    assert "scripts/**" not in codegen_filter


def test_opt_in_workflows_do_not_run_on_every_pr_sync() -> None:
    """An opt-in lane stays opt-in: reachable by label, not by every push.

    This used to also cover windows-cli-smoke.yml. That workflow is gone --
    ci.yml's `windows-cli` job was a strict superset of it, so it ran a second
    Windows runner to assert things the first one already had -- and the lane
    it guarded is now an ordinary path-filtered CI job rather than an opt-in
    one. surface-live is the remaining label-gated lane here.
    """
    e2e = _read(".github/workflows/e2e.yml")
    scenarios = _read(".github/workflows/scenarios.yml")

    assert "types: [labeled, synchronize" not in e2e
    assert "github.event.label.name == 'surface-live'" in e2e
    # The scenario lanes that boot a full stack are the other opt-in shape:
    # nightly, dispatch, or the run-scenarios label -- never every PR push.
    assert "run-scenarios" in scenarios


def test_backend_e2e_triggers_directly_without_a_label() -> None:
    """backend-e2e is the one opt-in-turned-mandatory exception here.

    It used to be gated behind a `run-e2e` label -- exactly the shape the
    sibling test above still requires of surface-live-smoke and windows-cli.
    It deliberately dropped that gate to run directly on every PR push
    instead, in parallel with "CI" rather than waiting on it (fast enough now
    at ~5-6 min, and a future required-check gate can't tolerate a workflow_run
    cascade turning a failed upstream run into a *skipped*, not failed, check).
    Pinning the absence of a label check here means a future edit that
    reintroduces one gets caught, the same way the sibling test catches it for
    the workflows still meant to have one.
    """
    e2e = _read(".github/workflows/e2e.yml")
    job = e2e.split("\n  backend-e2e:\n", 1)[1].split("\n  e2e-passed:", 1)[0]
    # Just the gate, not the whole job body -- the checkout step's ref:
    # fallback and its comment mention workflow_run harmlessly (it's simply
    # empty on any other trigger), which isn't the invariant this checks.
    condition = job.split("if: >-", 1)[1].split("runs-on:", 1)[0]

    # Logic, not prose: this job's own comments are free to say "label" (and
    # do elsewhere in the job body), so check the expression shapes a
    # label-gate or a workflow_run dependency would actually use, not the
    # bare word.
    assert "event.label" not in condition
    assert "== 'labeled'" not in condition
    assert "workflow_run" not in condition
    assert (
        "opened" in condition and "synchronize" in condition and "reopened" in condition
    )


def test_expensive_security_jobs_are_change_scoped() -> None:
    workflow = _read(".github/workflows/security.yml")

    assert "name: Detect security-relevant changes" in workflow
    assert "if: needs.changes.outputs.python == 'true'" in workflow
    assert "if: needs.changes.outputs.javascript == 'true'" in workflow
    assert "if: needs.changes.outputs.python_dependencies == 'true'" in workflow
    assert "if: needs.changes.outputs.backend_image == 'true'" in workflow


def test_every_job_that_installs_a_browser_restores_it_from_cache() -> None:
    """Chromium is downloaded once per Playwright version, not once per run.

    It is ~150 MB and byte-identical between runs, so three desktop jobs were
    each fetching it on every push. The cache has to sit *before* the install
    in the same job -- a restore afterwards is a restore of nothing -- and it
    has to be keyed on the lockfile, because that is the file a Playwright
    version bump changes.
    """
    import yaml

    workflow = yaml.safe_load(_read(".github/workflows/ci.yml"))
    installing = set()
    for name, job in workflow["jobs"].items():
        for index, step in enumerate(job.get("steps", [])):
            if "playwright install" not in str(step.get("run", "")):
                continue
            installing.add(name)
            cache = [
                earlier
                for earlier in job["steps"][:index]
                if earlier.get("uses", "").startswith("actions/cache@")
                and "ms-playwright" in str(earlier.get("with", {}).get("path", ""))
            ]
            assert cache, f"{name} downloads a browser it never restores"
            key = cache[-1]["with"]["key"]
            assert "desktop/ui-tests/package-lock.json" in key, (
                f"{name} keys its browser cache on something other than the "
                "lockfile a version bump changes"
            )
            paths = cache[-1]["with"]["path"]
            # One step for three runners: the browser lives somewhere different
            # on each, and a path that does not exist is skipped rather than
            # failing.
            for expected in (
                "~/.cache/ms-playwright",
                "~/Library/Caches/ms-playwright",
                "~/AppData/Local/ms-playwright",
            ):
                assert expected in paths, f"{name} misses {expected}"
    assert installing, "no job installs a browser; this contract found nothing"


def test_every_dmg_build_survives_a_busy_hdiutil() -> None:
    """`bundle_dmg.sh` fails on a busy `hdiutil`, and it fails late.

    By then the workspace has compiled, every test in the job has passed, and
    the only thing left is wrapping a signed `.app` in a disk image. That took
    main red once, and the re-run went green untouched.

    The retry is not enough on its own, which is the part worth pinning: a
    failed run leaves its image attached and a half-built bundle behind, so an
    attempt that does not clear both meets the last one's leftovers and fails
    the same way. Retrying an unchanged failure is how the first version of the
    apt retry managed three identical failures.
    """
    import yaml

    building = []
    for path in (
        ".github/workflows/ci.yml",
        ".github/workflows/release-local-images.yml",
    ):
        workflow = yaml.safe_load(_read(path))
        for name, job in workflow["jobs"].items():
            for step in job.get("steps", []):
                run = step.get("run") or ""
                # The steps that *invoke* the CLI to build, not the one that
                # puts its version in the environment — and not the
                # Windows-only `--bundles nsis` one, which makes no disk image.
                if '"$TAURI_CLI" build' not in run or "nsis" in run:
                    continue
                building.append((path, name))
                assert "for attempt in" in run, f"{name} bundles a DMG without retrying"
                assert "hdiutil detach" in run, (
                    f"{name} retries without detaching what the failure left "
                    "attached, so the retry asks the same broken question"
                )
    assert len(building) == 2, f"expected both DMG lanes, found {building}"
