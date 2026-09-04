"""App build/deploy step: pure helpers, the sandbox build (fake session), and
the runner's artifact selection by tier — all without a DB or a real sandbox."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest

from app.modules.pod_bundle.domain.errors import AppBuildFailedError
from app.modules.pod_bundle.infrastructure.app_builder import (
    AppSandboxBuilder,
    AppStepRunner,
    _clean_slug,
    classify_source_dir,
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
    def __init__(self, *, build_ok: bool = True, dist: bytes = b"DIST"):
        self.commands: list[str] = []
        self.files: dict[str, bytes] = {}
        self._build_ok = build_ok
        self._dist = dist

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def exec_command(self, *, cmd: str, timeout=None):
        self.commands.append(cmd)
        if "run build" in cmd:
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


async def test_a_portable_bundled_dist_is_deployed_without_rebuilding(tmp_path):
    """The exported build bakes in nothing pod-specific, so rebuilding it would
    spend minutes and a sandbox producing the same bytes."""
    resource = tmp_path / "apps" / "vite"
    _write_dist_bundle(resource, portable=True)

    source_bytes, dist_bytes = await _runner(_ExplodingSandbox())._artifacts(
        resource, "vite", app_slug="vite", pod_id=_POD, user_id=uuid4()
    )

    assert dist_bytes == b"EXPORTED-DIST"
    assert source_bytes  # source still ships, and is still the primary artifact


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
