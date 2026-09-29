"""What the runtime bundle must be, for the overlay to be safe to install.

The bundle's digest decides whether a sandbox reinstalls. That makes two
properties load-bearing rather than tidy: the digest must depend on the sources
and on nothing else, and the payload must actually contain what a sandbox
imports. Both have already failed once in development -- the digest moved
between builds because ``uv`` records the wheel's temporary path in
``direct_url.json``, and the ``lemma`` console script carried the shebang of the
build machine's interpreter, which is both broken inside a sandbox and a second
way for the build directory to reach the digest.

The real build is exercised rather than mocked. It costs a couple of seconds and
it is the only thing that would have caught either failure.
"""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
import zipfile
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


_BACKEND = Path(__file__).resolve().parents[2]
_MODULE_PATH = _BACKEND / "scripts" / "build_runtime_bundle.py"

_SPEC = spec_from_file_location("build_runtime_bundle", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
build_runtime_bundle = module_from_spec(_SPEC)
_SPEC.loader.exec_module(build_runtime_bundle)


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict, Path]:
    """One real build, shared by every test that only reads its output."""
    out_dir = tmp_path_factory.mktemp("bundle")
    manifest = build_runtime_bundle.build(out_dir)
    return manifest, out_dir


@pytest.fixture(scope="module")
def payload(built: tuple[dict, Path], tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The unpacked bundle root: `site-packages/` for imports, `bin/` for scripts."""
    _, out_dir = built
    extracted = tmp_path_factory.mktemp("payload")
    with zipfile.ZipFile(out_dir / "runtime-bundle.zip") as archive:
        archive.extractall(extracted)
    return extracted


@pytest.fixture(scope="module")
def site_packages(payload: Path) -> Path:
    return payload / "site-packages"


def test_two_builds_of_one_source_tree_are_byte_identical(
    tmp_path: Path, built: tuple[dict, Path]
) -> None:
    """The digest names the sources, or it names nothing worth comparing.

    A version that moves on its own reinstalls the fleet for no reason; one that
    fails to move leaves a sandbox running code we think we replaced.
    """
    first_manifest, first_dir = built
    second_manifest = build_runtime_bundle.build(tmp_path)

    assert first_manifest["version"] == second_manifest["version"]
    assert first_manifest["archive_sha256"] == second_manifest["archive_sha256"]
    assert (first_dir / "runtime-bundle.zip").read_bytes() == (
        tmp_path / "runtime-bundle.zip"
    ).read_bytes()


def test_the_version_is_the_digest_of_the_contents(built: tuple[dict, Path]) -> None:
    manifest, out_dir = built

    assert manifest["version"].startswith("sha256:")
    archive = (out_dir / "runtime-bundle.zip").read_bytes()
    assert manifest["archive_sha256"] == f"sha256:{hashlib.sha256(archive).hexdigest()}"
    # Two distinct digests on purpose: one names what the bundle *is*, the other
    # is what an integrity check on the wire compares.
    assert manifest["version"] != manifest["archive_sha256"]


def test_the_bundle_carries_every_package_a_sandbox_imports(
    site_packages: Path,
) -> None:
    for relative in build_runtime_bundle.REQUIRED_PACKAGES:
        package = site_packages / relative
        assert package.is_dir(), f"{relative} is missing from the bundle"
        assert any(package.iterdir()), f"{relative} is in the bundle but empty"


def test_the_vendored_skills_travel_with_the_cli(site_packages: Path) -> None:
    """``lemma-terminal`` 0.4.1 shipped with an empty skills package.

    The vendoring runs in ``lemma-cli/setup.py`` at build time, so nothing about
    the source tree says whether it worked -- only the built artifact does.
    """
    skills = sorted(
        path.parent.name for path in site_packages.glob("*/skills/*/SKILL.md")
    )

    assert skills, "the bundle carries no skills"
    assert "lemma-builder" in skills


def test_console_scripts_name_the_sandbox_interpreter(payload: Path) -> None:
    """Not the interpreter that happened to run the build.

    ``uv`` writes its own shebang, which inside a sandbox points at a path that
    does not exist. It also puts the build directory into the bundle's digest,
    so a laptop and CI would disagree about the version of identical code.
    """
    image_scripts = {
        command
        for commands in build_runtime_bundle.SCRIPT_NAMES.values()
        for command in commands
    }
    scripts = sorted(
        path for path in (payload / "bin").iterdir() if path.name not in image_scripts
    )

    assert scripts, "the bundle ships no console scripts"
    assert not (payload / "site-packages" / "bin").exists(), (
        "console scripts are nested inside site-packages, where they are not "
        "importable and nobody would look for them"
    )
    for script in scripts:
        shebang = script.read_text(encoding="utf-8").splitlines()[0]
        assert shebang == f"#!{build_runtime_bundle.SANDBOX_PYTHON}"


def test_a_windows_build_writes_the_same_unix_scripts(
    payload: Path, tmp_path: Path
) -> None:
    """The Windows host pack builds this bundle too, for a Linux sandbox.

    ``uv pip install --target`` writes scripts for the machine running it --
    ``.exe`` launchers on Windows -- so the scripts are written from the
    wheels' entry points instead, and must come out as a Unix build's do.
    """
    site_packages = tmp_path / "payload" / "site-packages"
    dist_info = site_packages / "lemma_terminal-0.0.0.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "entry_points.txt").write_text(
        "[console_scripts]\nlemma = lemma_cli.cli:main\n", encoding="utf-8"
    )
    installer_scripts = site_packages / "Scripts"
    installer_scripts.mkdir()
    (installer_scripts / "lemma.exe").write_bytes(b"MZ\x90\x00\xba launcher")

    build_runtime_bundle._write_console_scripts(site_packages, tmp_path / "payload")

    assert not installer_scripts.exists()
    written = tmp_path / "payload" / "bin" / "lemma"
    assert written.read_bytes() == (payload / "bin" / "lemma").read_bytes()


def test_no_file_in_the_bundle_names_the_machine_that_built_it(payload: Path) -> None:
    """The build directory must not survive anywhere in the payload.

    Not only in the shebang: ``direct_url.json`` and ``uv_cache.json`` both
    recorded it too, and each one on its own was enough to make the digest
    depend on where the build ran.
    """
    needle = str(_BACKEND).encode()
    offenders = [
        path.relative_to(payload).as_posix()
        for path in payload.rglob("*")
        if path.is_file() and needle in path.read_bytes()
    ]

    assert offenders == []


def test_record_agrees_with_what_the_bundle_actually_ships(site_packages: Path) -> None:
    """Every ``RECORD`` entry hashes a file that is present and unmodified.

    The console-script rewrite invalidates the hash ``uv`` wrote, so the build
    reseals ``RECORD`` afterwards. Without that the bundle would carry a
    manifest quietly disagreeing with its own contents.
    """
    stale: list[str] = []
    for record in site_packages.glob("*.dist-info/RECORD"):
        for line in record.read_text(encoding="utf-8").splitlines():
            relative, _, remainder = line.partition(",")
            expected = remainder.split(",")[0]
            if not relative or not expected:
                continue
            target = site_packages / relative
            if not target.is_file():
                stale.append(f"{relative} (missing)")
                continue
            digest = base64.urlsafe_b64encode(
                hashlib.sha256(target.read_bytes()).digest()
            )
            if f"sha256={digest.decode('ascii').rstrip('=')}" != expected:
                stale.append(f"{relative} (hash)")

    assert stale == []


def test_the_manifest_names_the_archive_beside_it(built: tuple[dict, Path]) -> None:
    manifest, out_dir = built
    on_disk = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))

    assert on_disk["version"] == manifest["version"]
    assert (out_dir / on_disk["archive"]).is_file()
    assert on_disk["requires"] == list(build_runtime_bundle.REQUIRED_IMPORTS)


def test_the_bundle_ships_no_second_copy_of_the_pod_bundle(site_packages: Path) -> None:
    """``lemma-terminal`` already vendors ``lemma_pod_bundle`` into its wheel.

    Building ``lemma-pod-bundle`` as a third wheel would unpack a competing copy
    of the same top-level package into the same directory, and which one won
    would depend on install order.
    """
    dist_infos = sorted(path.name for path in site_packages.glob("*.dist-info"))

    assert not any(name.startswith("lemma_pod_bundle-") for name in dist_infos)
    assert (site_packages / "lemma_pod_bundle").is_dir()


def test_the_manifest_names_the_release_it_was_built_from(
    built: tuple[dict, Path],
) -> None:
    """So a log line can say which release a sandbox is running.

    The digest is the identity and is the stronger claim -- two builds of one
    version can differ, two builds of one digest cannot -- but "0.8.0" is what a
    person reads, and needing to resolve a hash to answer "is this sandbox on the
    current release" is how the fleet's staleness stayed invisible before.
    """
    manifest, _ = built
    declared = (_BACKEND.parent / "lemma-python" / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    expected = next(
        line.split("=", 1)[1].strip().strip('"')
        for line in declared.splitlines()
        if line.startswith("version")
    )

    assert manifest["component_version"] == expected


def test_the_backend_image_builds_the_bundle_from_its_own_sources(
    built: tuple[dict, Path],
) -> None:
    """The coupling this whole design rests on, asserted statically.

    A sandbox cannot be replaced to give it newer code, so it is *sent* the
    code -- and what it is sent has to be what the server expects to talk to.
    Building the bundle inside the backend image makes that a property of the
    build rather than something an operator remembers: one deploy moves both,
    one rollback rolls both back. Publishing it separately would reintroduce the
    two-artifact promotion this exists to remove.
    """
    del built
    dockerfile = (_BACKEND / "Dockerfile").read_text(encoding="utf-8")

    assert "scripts/build_runtime_bundle.py" in dockerfile, (
        "the backend image does not build the bundle, so every deployment "
        "loads none and no sandbox is ever updated"
    )
    assert "/app/runtime-bundle" in dockerfile
    # lemma-cli's setup.py vendors ../lemma-skills and ../lemma-pod-bundle at
    # build time, so the first-party projects have to sit beside each other.
    for project in ("lemma-python", "lemma-cli", "lemma-skills"):
        assert f"COPY {project} /{project}" in dockerfile, (
            f"{project} is not beside the others in the builder stage"
        )


#: Modules a sandbox process imports after the overlay is installed, beyond the
#: ones the installer's own smoke test proves. Each is reached by a real
#: process: the relay through `browser_relay.chrome`, `execute_python` through
#: the Python worker, loopback fall-through through `host_fallback`.
_IMPORTED_UNDER_THE_OVERLAY = (
    "sandbox_runtime.paths",
    "sandbox_runtime.browser_relay.chrome",
    "sandbox_runtime.workspace.python_worker",
    "sandbox_runtime.host_fallback",
)


def test_an_installed_overlay_leaves_every_sandbox_module_importable(
    site_packages: Path,
) -> None:
    """The overlay sits at ``sys.path[0]`` and the image's own copy comes last.

    ``sandbox_runtime`` is a regular package, so the first directory that has
    it wins outright: Python never looks in the image's copy for a submodule
    the overlay lacks. A bundle carrying part of the package therefore does
    not add to the image's copy, it replaces it, and every module left out
    stops existing the moment the overlay is installed.
    """
    program = (
        "import importlib, sys\n"
        f"sys.path.insert(0, {str(site_packages)!r})\n"
        f"sys.path.append({str(_BACKEND)!r})\n"
        "failed = []\n"
        f"for name in {list(_IMPORTED_UNDER_THE_OVERLAY)!r}:\n"
        "    try:\n"
        "        importlib.import_module(name)\n"
        "    except ImportError as exc:\n"
        "        failed.append(f'{name}: {exc}')\n"
        "sys.exit('; '.join(failed) if failed else 0)\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", program],
        capture_output=True,
        text=True,
        cwd=site_packages.parent,
    )

    assert result.returncode == 0, result.stderr


def _load_import_resolver():
    path = Path(__file__).with_name("test_e2b_templates_ship_their_imports.py")
    spec = spec_from_file_location("sandbox_import_resolver", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_bundle_import_closure(site_packages: Path) -> None:
    """Every ``sandbox_runtime`` module the bundle ships finds its imports there.

    Read from the built archive, not from ``RUNTIME_SOURCES``: the question is
    what a sandbox gets, and a module the build dropped would be as missing as
    one nobody listed.
    """
    resolver = _load_import_resolver()
    shipped = {
        path.relative_to(site_packages)
        .with_suffix("")
        .as_posix()
        .replace("/", ".")
        .removesuffix(".__init__")
        for path in (site_packages / "sandbox_runtime").rglob("*.py")
    }

    missing = {
        f"{module} -> {needed}"
        for module in sorted(shipped)
        for needed in resolver._imports_of(module)
        if needed not in shipped
    }

    assert not missing, f"the bundle ships modules whose imports it does not: {missing}"


def test_the_installer_proves_every_required_module_imports(
    site_packages: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What ``runtime_install._smoke_test`` imports, with the overlay in front.

    The same program the installer runs inside the sandbox, so a module named
    in ``requires`` that cannot import from the bundle fails here first rather
    than on every sandbox the release reaches. Run from an empty directory, as
    the installer is: ``python -c`` puts the working directory ahead of
    everything, and this checkout's own ``sandbox_runtime`` would win.
    """
    from sandbox_runtime import runtime_install

    monkeypatch.chdir(tmp_path)
    runtime_install._smoke_test(
        site_packages, list(build_runtime_bundle.REQUIRED_IMPORTS)
    )


def test_every_workspace_side_module_is_in_the_bundle() -> None:
    """A new module under ``sandbox_runtime`` is shipped or excluded on purpose.

    Leaving one out is not a smaller bundle. The overlay replaces the image's
    copy of the package, so the module stops existing in every sandbox that
    has the overlay.
    """
    decided = {
        *build_runtime_bundle.RUNTIME_SOURCES,
        *build_runtime_bundle.RUNTIME_EXCLUDED,
    }
    present = {
        path.relative_to(_BACKEND).as_posix()
        for path in (_BACKEND / "sandbox_runtime").iterdir()
        if path.name != "__pycache__"
    }

    assert present - decided == set()
    assert decided - present == set()


def test_the_image_scripts_travel_with_the_overlay(
    payload: Path, built: tuple[dict, Path]
) -> None:
    """Under the names the images install them as, executable, with LF endings."""
    _, out_dir = built
    with zipfile.ZipFile(out_dir / "runtime-bundle.zip") as archive:
        modes = {info.filename: info.external_attr >> 16 for info in archive.infolist()}
    for name, commands in build_runtime_bundle.SCRIPT_NAMES.items():
        source = _BACKEND / build_runtime_bundle.SCRIPTS_DIRECTORY / name
        for command in commands:
            shipped = payload / "bin" / command
            assert shipped.is_file(), f"bin/{command} is missing"
            assert modes[f"bin/{command}"] == 0o755, f"bin/{command} is not executable"
            assert shipped.read_bytes() == source.read_bytes().replace(b"\r\n", b"\n")
    for name in build_runtime_bundle.SCRIPT_LIBRARIES:
        assert (payload / "lib" / name).is_file()
    for name in build_runtime_bundle.IMAGE_ONLY_SCRIPTS:
        assert not (payload / "bin" / name.removesuffix(".sh")).exists()


def test_a_script_nobody_classified_stops_the_build(tmp_path: Path) -> None:
    backend = tmp_path / "backend"
    scripts = backend / build_runtime_bundle.SCRIPTS_DIRECTORY
    scripts.mkdir(parents=True)
    for name in (
        *build_runtime_bundle.SCRIPT_NAMES,
        *build_runtime_bundle.SCRIPT_LIBRARIES,
        *build_runtime_bundle.IMAGE_ONLY_SCRIPTS,
        "brand-new-helper.sh",
    ):
        (scripts / name).write_text("#!/bin/sh\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="brand-new-helper.sh"):
        build_runtime_bundle._copy_scripts(tmp_path / "payload", backend)


def test_a_crlf_checkout_ships_the_same_scripts(tmp_path: Path) -> None:
    """A Windows checkout may have rewritten every line ending."""
    backend = tmp_path / "backend"
    scripts = backend / build_runtime_bundle.SCRIPTS_DIRECTORY
    scripts.mkdir(parents=True)
    for name in (
        *build_runtime_bundle.SCRIPT_NAMES,
        *build_runtime_bundle.SCRIPT_LIBRARIES,
        *build_runtime_bundle.IMAGE_ONLY_SCRIPTS,
    ):
        (scripts / name).write_bytes(b"#!/bin/sh\r\necho ok\r\n")

    build_runtime_bundle._copy_scripts(tmp_path / "payload", backend)

    assert (tmp_path / "payload" / "bin" / "save-webpage").read_bytes() == (
        b"#!/bin/sh\necho ok\n"
    )
