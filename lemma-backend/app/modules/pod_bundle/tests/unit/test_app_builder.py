"""App build/deploy step: pure helpers, the sandbox build (fake session), and
the runner's artifact selection by tier — all without a DB or a real sandbox."""

from __future__ import annotations

import io
import json
import os
import shlex
import subprocess
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from app.modules.pod_bundle.domain.errors import AppBuildFailedError
from app.modules.pod_bundle.domain.state import PlanStep, StepAction, StepKind
from app.modules.pod_bundle.infrastructure.app_build_output import (
    BUILD_LOG_DETAIL,
    build_log_kept_on,
    build_log_of,
    failure_line,
    output_tail,
)
from app.modules.pod_bundle.infrastructure.app_builder import (
    DEFAULT_PNPM_WORKSPACE,
    AppSandboxBuilder,
    AppStepRunner,
    _clean_slug,
    build_script,
    classify_source_dir,
    workspace_for,
    slug_candidates,
    zip_dir,
)

_POD = uuid4()


# --- pure helpers ------------------------------------------------------------


def test_classify_vite(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    assert classify_source_dir(tmp_path) == "vite"


def test_classify_static(tmp_path):
    (tmp_path / "index.html").write_text("<h1>hi</h1>")
    assert classify_source_dir(tmp_path) == "static"


def test_classify_raises_when_neither(tmp_path):
    with pytest.raises(AppBuildFailedError):
        classify_source_dir(tmp_path)


def test_zip_dir_excludes_build_junk(tmp_path):
    (tmp_path / "index.html").write_text("root")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.js").write_text("junk")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.ts").write_text("x")
    with zipfile.ZipFile(io.BytesIO(zip_dir(tmp_path))) as zf:
        names = set(zf.namelist())
    assert "index.html" in names
    assert "src/main.ts" in names
    assert not any(n.startswith("node_modules/") for n in names)


def test_slug_candidates_are_deterministic():
    a = slug_candidates("My App", pod_id=_POD, app_name="app")
    b = slug_candidates("My App", pod_id=_POD, app_name="app")
    assert a == b  # replay must resolve to the same slug sequence
    assert len(a) >= 3
    assert "${" not in a[0]
    # The fallbacks carry a stable hash suffix distinct from the base.
    assert a[1] != a[0]


def test_clean_slug_drops_placeholder_and_empty():
    assert _clean_slug("${dashboard_slug}") is None
    assert _clean_slug("") is None
    assert _clean_slug("   ") is None
    assert _clean_slug("good-slug") == "good-slug"


# --- sandbox build (fake session) -------------------------------------------


class _FakeSession:
    def __init__(
        self,
        *,
        build_ok: bool = True,
        dist: bytes = b"DIST",
        build_result: dict[str, object] | None = None,
    ):
        self.commands: list[str] = []
        self.files: dict[str, bytes] = {}
        self._build_ok = build_ok
        self._dist = dist
        self._build_result = build_result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def exec_command(self, *, cmd: str, timeout=None):
        self.commands.append(cmd)
        if "run build" in cmd:
            if self._build_result is not None:
                return self._build_result
            return {"success": self._build_ok, "stdout": "", "stderr": "boom"}
        if cmd.startswith("test -f"):
            return {"success": self._build_ok}
        return {"success": True, "stdout": ""}

    async def write_file(self, path: str, data: bytes, *, timeout: int):
        del timeout
        self.files[path] = data

    async def read_file(self, path: str, *, timeout: int):
        del timeout
        assert path.endswith("/dist.zip")
        return self._dist


class _FakeWorkspace:
    def __init__(self, session):
        self._session = session
        self.env: dict | None = None

    async def get_session(self, **kwargs):
        self.env = kwargs.get("env_vars")
        return self._session


async def test_build_injects_target_pod_env_and_returns_dist():
    session = _FakeSession(build_ok=True, dist=b"BUILT-DIST-BYTES")
    ws = _FakeWorkspace(session)
    out = await AppSandboxBuilder(ws).build(
        user_id=uuid4(), pod_id=_POD, app_slug="my-app", source_zip=b"source"
    )
    assert out == b"BUILT-DIST-BYTES"
    # The build bakes the TARGET pod id (the whole reason a rebuild is required).
    assert ws.env["VITE_LEMMA_POD_ID"] == str(_POD)
    assert "VITE_LEMMA_API_URL" in ws.env
    assert any("run build" in c for c in session.commands)


async def test_build_tool_failure_is_terminal():
    session = _FakeSession(build_ok=False)
    with pytest.raises(AppBuildFailedError):
        await AppSandboxBuilder(_FakeWorkspace(session)).build(
            user_id=uuid4(), pod_id=_POD, app_slug="a", source_zip=b"source"
        )


# --- what a failed build tells the person -------------------------------------

# What pnpm 11 printed for a Vite app that uses the Lemma SDK and brought no
# pnpm-workspace.yaml: the error on stdout, nothing on stderr.
_PNPM_IGNORED_BUILDS = {
    "success": False,
    "completed": True,
    "stdout": (
        "Packages: +31\n"
        "+ vite 6.4.6\n\n"
        "[ERR_PNPM_IGNORED_BUILDS] Ignored build scripts: browser-tabs-lock@1.3.0, "
        "esbuild@0.25.12\n\n"
        'Run "pnpm approve-builds" to pick which dependencies should be allowed '
        "to run scripts.\n"
    ),
    "stderr": "",
    "error": "The command exited 1.",
}

# What a Vite build that could not parse its entry printed: the cause on
# stderr above a stack whose frames are named `error`, and pnpm's own
# one-line verdict on stdout.
_VITE_PARSE_FAILURE = {
    "success": False,
    "completed": True,
    "stdout": (
        "vite v6.4.6 building for production...\n"
        "\x1b[32m✓\x1b[39m 0 modules transformed.\n"
        "[ELIFECYCLE] Command failed with exit code 1.\n"
    ),
    "stderr": (
        "$ vite build\n"
        "✗ Build failed in 20ms\n"
        "error during build:\n"
        "[vite:build-html] main.js (1:60): Failed to parse source for import "
        "analysis because the content contains invalid JS syntax.\n"
        "file: /src/main.js:1:60\n"
        "    at getRollupError (file:///src/node_modules/rollup/parseAst.js:355:41)\n"
        "    at error (file:///src/node_modules/rollup/parseAst.js:351:42)\n"
    ),
    "error": "The command exited 1.",
}


def test_a_failure_line_is_found_on_stdout_when_stderr_has_none():
    assert failure_line(_PNPM_IGNORED_BUILDS) == (
        "[ERR_PNPM_IGNORED_BUILDS] Ignored build scripts: browser-tabs-lock@1.3.0, "
        "esbuild@0.25.12"
    )


def test_a_failure_line_skips_stack_frames_and_pnpms_exit_verdict():
    assert failure_line(_VITE_PARSE_FAILURE) == (
        "[vite:build-html] main.js (1:60): Failed to parse source for import "
        "analysis because the content contains invalid JS syntax."
    )


def test_a_failure_with_no_output_falls_back_to_how_the_command_ended():
    result = {"success": False, "stdout": "", "stderr": "", "error": "Timed out."}
    assert failure_line(result) == "Timed out."
    assert failure_line({"success": False}) is None


def test_the_log_keeps_the_end_of_both_streams_within_its_limit():
    result = {
        "stdout": "progress\n" * 2000 + "ERR_PNPM_FETCH_404 GET /left-pad: Not Found",
        "stderr": "WARN deprecated inflight@1.0.6",
        "error": "The command exited 1.",
    }

    log = output_tail(result)

    assert len(log) <= 4000
    assert "[stdout]" in log and "[stderr]" in log and "[error]" in log
    # The short streams are whole; the long one gets the room they left.
    assert "WARN deprecated inflight@1.0.6" in log
    assert log.split("[stderr]")[0].rstrip().endswith("Not Found")
    assert len(log) > 3900


def test_the_log_splits_its_room_between_two_long_streams():
    log = output_tail({"stdout": "o" * 5000, "stderr": "e" * 5000})
    assert len(log) <= 4000
    assert log.count("o") > 1900 and log.count("e") > 1900


async def test_a_failed_build_names_its_cause_and_carries_both_streams():
    session = _FakeSession(build_result=_PNPM_IGNORED_BUILDS)

    with pytest.raises(AppBuildFailedError) as caught:
        await AppSandboxBuilder(_FakeWorkspace(session)).build(
            user_id=uuid4(), pod_id=_POD, app_slug="a", source_zip=b"source"
        )

    assert str(caught.value) == (
        "App build failed: [ERR_PNPM_IGNORED_BUILDS] Ignored build scripts: "
        "browser-tabs-lock@1.3.0, esbuild@0.25.12"
    )
    log = build_log_of(caught.value)
    assert log is not None
    assert "[stdout]" in log and "pnpm approve-builds" in log
    assert "The command exited 1." in log


async def test_a_build_still_running_when_the_wait_ends_is_a_failure():
    """The session answers a command it stopped waiting on as successful and
    not completed; read as success it surfaced as a missing dist/index.html."""
    session = _FakeSession(
        build_result={"success": True, "completed": False, "stdout": "transforming"}
    )

    with pytest.raises(AppBuildFailedError, match="did not finish within"):
        await AppSandboxBuilder(_FakeWorkspace(session)).build(
            user_id=uuid4(), pod_id=_POD, app_slug="a", source_zip=b"source"
        )


def _step(**detail: object) -> PlanStep:
    return PlanStep(
        index=0,
        kind=StepKind.APP,
        name="dashboard",
        action=StepAction.CREATE,
        detail=dict(detail),
    )


def test_a_failed_build_leaves_its_log_on_the_step():
    step = _step(visibility="POD")

    with pytest.raises(AppBuildFailedError):
        with build_log_kept_on(step):
            raise AppBuildFailedError("App build failed.", details={"log": "the log"})

    assert step.detail == {"visibility": "POD", BUILD_LOG_DETAIL: "the log"}


def test_a_retry_that_builds_drops_the_last_attempts_log():
    """A re-apply resets the step to PENDING but not its detail."""
    step = _step(visibility="POD", **{BUILD_LOG_DETAIL: "the last attempt"})

    with build_log_kept_on(step):
        pass

    assert step.detail == {"visibility": "POD"}


def test_a_failure_with_no_build_output_adds_nothing_to_the_step():
    step = _step()

    with pytest.raises(AppBuildFailedError):
        with build_log_kept_on(step):
            raise AppBuildFailedError("Could not allocate a unique public slug.")

    assert step.detail == {}


# --- the build script, run by a shell -----------------------------------------


def _run_build_script(
    src: Path, tmp_path: Path, *, workspace: str = DEFAULT_PNPM_WORKSPACE
) -> list[str]:
    """Run the real script with a ``pnpm`` that records what it was asked."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "pnpm-calls"
    pnpm = bin_dir / "pnpm"
    pnpm.write_text(f'#!/bin/sh\necho "$*" >> {shlex.quote(str(calls))}\n')
    pnpm.chmod(0o755)
    subprocess.run(
        ["bash", "-c", build_script(str(src), workspace=workspace)],
        check=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )
    return calls.read_text().splitlines()


def test_an_app_without_a_workspace_file_is_built_with_esbuild_allowed(tmp_path):
    src = tmp_path / "src dir"  # a space, so the quoting is exercised too
    src.mkdir()

    calls = _run_build_script(src, tmp_path)

    written = (src / "pnpm-workspace.yaml").read_text()
    assert written == DEFAULT_PNPM_WORKSPACE
    settings = yaml.safe_load(written)
    assert settings["onlyBuiltDependencies"] == ["esbuild"]
    assert settings["allowBuilds"] == {"esbuild": True}
    assert settings["strictDepBuilds"] is False
    assert calls == ["install --no-frozen-lockfile", "run build"]


def test_an_apps_own_workspace_file_is_left_as_it_is(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    own = "allowBuilds:\n  esbuild: true\n  sharp: true\n"
    (src / "pnpm-workspace.yaml").write_text(own)
    (src / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")

    calls = _run_build_script(src, tmp_path)

    assert (src / "pnpm-workspace.yaml").read_text() == own
    assert calls == ["install --frozen-lockfile", "run build"]


def _zipped(package_json: str | None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("index.html", "<html></html>")
        if package_json is not None:
            archive.writestr("package.json", package_json)
    return buffer.getvalue()


@pytest.mark.parametrize(
    "package_json",
    [
        # The same names outside `pnpm` say nothing about install scripts.
        '{"config": {"allowBuilds": false}}',
        '{"name": "onlyBuiltDependencies"}',
        '{"pnpm": "onlyBuiltDependencies"}',
        '{"pnpm": {"overrides": {"a": "1"}}}',
        "not json",
        None,
    ],
)
def test_an_app_with_no_policy_of_its_own_gets_the_default(package_json):
    assert workspace_for(_zipped(package_json)) == DEFAULT_PNPM_WORKSPACE


def test_an_apps_pnpm_policy_is_carried_where_pnpm_11_reads_it():
    """pnpm 11 ignores `package.json -> pnpm`; the policy it gave there is
    written to the workspace file, over the default's esbuild."""
    written = yaml.safe_load(
        workspace_for(
            _zipped(
                json.dumps(
                    {
                        "pnpm": {
                            "onlyBuiltDependencies": ["sharp"],
                            "neverBuiltDependencies": ["core-js"],
                            "allowBuilds": {"@swc/core": True, "odd": "yes"},
                        }
                    }
                )
            )
        )
    )

    assert written == {
        "onlyBuiltDependencies": ["@swc/core", "esbuild", "sharp"],
        "allowBuilds": {
            "@swc/core": True,
            "core-js": False,
            "esbuild": True,
            "sharp": True,
        },
        "strictDepBuilds": False,
    }


def test_an_app_may_still_refuse_esbuild_by_name():
    written = yaml.safe_load(
        workspace_for(_zipped('{"pnpm": {"allowBuilds": {"esbuild": false}}}'))
    )
    assert written["allowBuilds"] == {"esbuild": False}
    assert written["onlyBuiltDependencies"] == []


def test_the_carried_policy_is_written_when_the_app_brings_no_file(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    carried = workspace_for(
        _zipped('{"pnpm": {"onlyBuiltDependencies": ["sharp", "esbuild"]}}')
    )

    calls = _run_build_script(src, tmp_path, workspace=carried)

    assert (src / "pnpm-workspace.yaml").read_text() == carried
    assert yaml.safe_load(carried)["allowBuilds"] == {"esbuild": True, "sharp": True}
    assert calls == ["install --no-frozen-lockfile", "run build"]


# --- runner artifact selection by tier ---------------------------------------


class _ExplodingSandbox:
    async def build(self, **kwargs):
        raise AssertionError("no sandbox build should run for this tier")


class _RecordingSandbox:
    """A sandbox that records that it WAS needed."""

    result = b"REBUILT"

    def __init__(self):
        self.calls = 0

    async def build(self, **kwargs):
        self.calls += 1
        return self.result


def _runner(sandbox):
    return AppStepRunner(uow_factory=object(), sandbox_builder=sandbox)


async def test_artifacts_static_deploys_source_as_dist(tmp_path):
    resource = tmp_path / "apps" / "site"
    (resource / "source").mkdir(parents=True)
    (resource / "source" / "index.html").write_text("<h1>hi</h1>")

    source_bytes, dist_bytes = await _runner(_ExplodingSandbox())._artifacts(
        resource, "site", app_slug="site", pod_id=_POD, user_id=uuid4()
    )
    # Static tier: no build; the served dist IS the source, with index.html at root.
    assert source_bytes == dist_bytes
    with zipfile.ZipFile(io.BytesIO(dist_bytes)) as zf:
        assert "index.html" in zf.namelist()


async def test_artifacts_vite_builds_in_sandbox(tmp_path):
    resource = tmp_path / "apps" / "app"
    (resource / "source").mkdir(parents=True)
    (resource / "source" / "package.json").write_text("{}")
    (resource / "source" / "index.html").write_text("<div id=root></div>")

    class _Sandbox:
        def __init__(self):
            self.call: dict | None = None

        async def build(self, *, user_id, pod_id, app_slug, source_zip):
            self.call = {"app_slug": app_slug, "pod_id": pod_id}
            return b"BUILT"

    sandbox = _Sandbox()
    source_bytes, dist_bytes = await _runner(sandbox)._artifacts(
        resource, "app", app_slug="app-x", pod_id=_POD, user_id=uuid4()
    )
    assert dist_bytes == b"BUILT"
    assert source_bytes is not None  # vite ships source for re-builds
    assert sandbox.call == {"app_slug": "app-x", "pod_id": _POD}


async def test_artifacts_single_html_file_is_its_own_source(tmp_path):
    """`html.html` is the CLI bundle format's one-file no-build app. The server
    importer used to see no source/ and no dist.zip and fail a bundle the CLI
    imports happily."""
    resource = tmp_path / "apps" / "page"
    resource.mkdir(parents=True)
    (resource / "html.html").write_text("<h1>hi</h1>")

    source_bytes, dist_bytes = await _runner(_ExplodingSandbox())._artifacts(
        resource, "page", app_slug="page", pod_id=_POD, user_id=uuid4()
    )

    assert source_bytes == dist_bytes
    with zipfile.ZipFile(io.BytesIO(dist_bytes)) as zf:
        assert zf.namelist() == ["index.html"]
        assert zf.read("index.html").decode() == "<h1>hi</h1>"


async def test_artifacts_dist_zip_fallback_when_no_source(tmp_path):
    resource = tmp_path / "apps" / "widget"
    resource.mkdir(parents=True)
    (resource / "dist.zip").write_bytes(b"PREBUILT-DIST")

    source_bytes, dist_bytes = await _runner(_ExplodingSandbox())._artifacts(
        resource, "widget", app_slug="widget", pod_id=_POD, user_id=uuid4()
    )
    # A prebuilt site has no build step behind it, so the build is also its
    # source. Uploading no source here left the imported app source-less, so the
    # target pod's next export dropped the app's code.
    assert source_bytes == b"PREBUILT-DIST"
    assert dist_bytes == b"PREBUILT-DIST"


async def test_artifacts_missing_everything_raises(tmp_path):
    resource = tmp_path / "apps" / "empty"
    resource.mkdir(parents=True)
    with pytest.raises(AppBuildFailedError):
        await _runner(_ExplodingSandbox())._artifacts(
            resource, "empty", app_slug="empty", pod_id=_POD, user_id=uuid4()
        )


def _write_dist_bundle(resource: Path, *, portable: bool | None, package_json=True):
    """A bundle carrying source AND a build, as exports now produce."""
    source = resource / "source"
    source.mkdir(parents=True, exist_ok=True)
    if package_json:
        (source / "package.json").write_text('{"name":"a"}')
    (source / "index.html").write_text("<html></html>")
    (resource / "dist.zip").write_bytes(b"EXPORTED-DIST")
    if portable is not None:
        (resource / "dist.json").write_text(json.dumps({"portable": portable}))


@pytest.mark.parametrize("marker", [{"portable": True}, [], None, "invalid"])
async def test_vite_build_is_rebuilt_even_when_marker_claims_portability(
    tmp_path, marker
):
    resource = tmp_path / "apps" / "vite"
    _write_dist_bundle(resource, portable=True)
    (resource / "dist.json").write_text(json.dumps(marker))
    sandbox = _RecordingSandbox()
    source_bytes, dist_bytes = await _runner(sandbox)._artifacts(
        resource, "vite", app_slug="vite", pod_id=_POD, user_id=uuid4()
    )
    assert dist_bytes == sandbox.result
    assert sandbox.calls == 1
    assert source_bytes


async def test_a_non_portable_bundled_dist_is_rebuilt(tmp_path):
    """The build hardcoded the source pod, so deploying it would ship an app
    pointed at someone else's data."""
    resource = tmp_path / "apps" / "vite"
    _write_dist_bundle(resource, portable=False)
    sandbox = _RecordingSandbox()

    _source_bytes, dist_bytes = await _runner(sandbox)._artifacts(
        resource, "vite", app_slug="vite", pod_id=_POD, user_id=uuid4()
    )

    assert dist_bytes == sandbox.result
    assert sandbox.calls == 1


async def test_a_bundle_without_the_portability_marker_is_rebuilt(tmp_path):
    """Exported before dist.json existed. Falling back to a rebuild is exactly
    what happened before builds were exported at all."""
    resource = tmp_path / "apps" / "vite"
    _write_dist_bundle(resource, portable=None)
    sandbox = _RecordingSandbox()

    _source_bytes, dist_bytes = await _runner(sandbox)._artifacts(
        resource, "vite", app_slug="vite", pod_id=_POD, user_id=uuid4()
    )

    assert dist_bytes == sandbox.result
    assert sandbox.calls == 1


async def test_a_static_app_ignores_the_bundled_dist_and_serves_its_source(tmp_path):
    """A static site has no build step: its source IS the served site, so a
    stale exported dist must not take precedence over it."""
    resource = tmp_path / "apps" / "static"
    _write_dist_bundle(resource, portable=True, package_json=False)

    source_bytes, dist_bytes = await _runner(_ExplodingSandbox())._artifacts(
        resource, "static", app_slug="static", pod_id=_POD, user_id=uuid4()
    )

    assert dist_bytes == source_bytes
    assert dist_bytes != b"EXPORTED-DIST"
