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
    _, out_dir = built
    extracted = tmp_path_factory.mktemp("payload")
    with zipfile.ZipFile(out_dir / "runtime-bundle.zip") as archive:
        archive.extractall(extracted)
    return extracted / "site-packages"


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


def test_the_bundle_carries_every_package_a_sandbox_imports(payload: Path) -> None:
    for relative in build_runtime_bundle.REQUIRED_PACKAGES:
        package = payload / relative
        assert package.is_dir(), f"{relative} is missing from the bundle"
        assert any(package.iterdir()), f"{relative} is in the bundle but empty"


def test_the_vendored_skills_travel_with_the_cli(payload: Path) -> None:
    """``lemma-terminal`` 0.4.1 shipped with an empty skills package.

    The vendoring runs in ``lemma-cli/setup.py`` at build time, so nothing about
    the source tree says whether it worked -- only the built artifact does.
    """
    skills = sorted(path.parent.name for path in payload.glob("*/skills/*/SKILL.md"))

    assert skills, "the bundle carries no skills"
    assert "lemma-builder" in skills


def test_console_scripts_name_the_sandbox_interpreter(payload: Path) -> None:
    """Not the interpreter that happened to run the build.

    ``uv`` writes its own shebang, which inside a sandbox points at a path that
    does not exist. It also puts the build directory into the bundle's digest,
    so a laptop and CI would disagree about the version of identical code.
    """
    scripts = sorted((payload / "bin").iterdir())

    assert scripts, "the bundle ships no console scripts"
    for script in scripts:
        shebang = script.read_text(encoding="utf-8").splitlines()[0]
        assert shebang == f"#!{build_runtime_bundle.SANDBOX_PYTHON}"


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


def test_record_agrees_with_what_the_bundle_actually_ships(payload: Path) -> None:
    """Every ``RECORD`` entry hashes a file that is present and unmodified.

    The console-script rewrite invalidates the hash ``uv`` wrote, so the build
    reseals ``RECORD`` afterwards. Without that the bundle would carry a
    manifest quietly disagreeing with its own contents.
    """
    stale: list[str] = []
    for record in payload.glob("*.dist-info/RECORD"):
        for line in record.read_text(encoding="utf-8").splitlines():
            relative, _, remainder = line.partition(",")
            expected = remainder.split(",")[0]
            if not relative or not expected:
                continue
            target = payload / relative
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


def test_the_bundle_ships_no_second_copy_of_the_pod_bundle(payload: Path) -> None:
    """``lemma-terminal`` already vendors ``lemma_pod_bundle`` into its wheel.

    Building ``lemma-pod-bundle`` as a third wheel would unpack a competing copy
    of the same top-level package into the same directory, and which one won
    would depend on install order.
    """
    dist_infos = sorted(path.name for path in payload.glob("*.dist-info"))

    assert not any(name.startswith("lemma_pod_bundle-") for name in dist_infos)
    assert (payload / "lemma_pod_bundle").is_dir()
