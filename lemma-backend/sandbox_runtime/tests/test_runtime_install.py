"""The installer runs where nobody can watch it, so it is tested here instead.

Everything this module does happens inside a sandbox, as root, with no logs
anyone reads and no way to attach a debugger. The failure it must never produce
is a ``current`` symlink pointing at a tree that does not work -- that is a
workspace which provisions successfully and then fails every operation, which is
exactly the shape of the incident this whole change exists to prevent.

So the invariant the tests are built around is: **``current`` only ever moves
after the smoke test passes.** Everything else -- idempotence, racing
installers, pruning -- is about not needing a lock.
"""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from sandbox_runtime import runtime_install


def _bundle(tmp_path: Path, *, name: str, body: str, marker: str = "") -> Path:
    """A minimal archive shaped like a real one: a package under site-packages."""
    archive = tmp_path / f"{name}.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(f"site-packages/{name}/__init__.py", body)
        if marker:
            bundle.writestr(f"site-packages/{marker}", "")
    return archive


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "lemma-runtime"


@pytest.fixture
def site(tmp_path: Path) -> Path:
    directory = tmp_path / "site-packages"
    directory.mkdir()
    return directory


_V1 = "sha256:" + "1" * 64
_V2 = "sha256:" + "2" * 64


def test_an_empty_root_reports_nothing_installed(root: Path, site: Path) -> None:
    assert runtime_install.probe(root, site_packages=site)["version"] is None


def test_probe_reports_whether_a_baked_copy_is_behind_the_overlay(
    root: Path, site: Path
) -> None:
    """The backend's failure policy turns on this, not on taste.

    With a baked copy present a failed install can degrade to it; without one the
    sandbox has no ``lemma`` at all and the ensure has to fail instead.
    """
    assert runtime_install.probe(root, site_packages=site)["baked"] is False

    (site / "lemma_cli").mkdir()

    assert runtime_install.probe(root, site_packages=site)["baked"] is True


def test_installing_makes_the_bundle_importable_and_recorded(
    tmp_path: Path, root: Path, site: Path
) -> None:
    archive = _bundle(tmp_path, name="probe_pkg", body="VALUE = 'first'")

    outcome = runtime_install.install(
        root=root,
        archive=archive,
        version=_V1,
        requires=["probe_pkg"],
        site_packages=site,
    )

    assert outcome == {"version": _V1, "installed": True}
    assert runtime_install.probe(root, site_packages=site)["version"] == _V1
    assert (root / "current" / "site-packages" / "probe_pkg").is_dir()


def test_installing_the_same_version_again_does_no_work(
    tmp_path: Path, root: Path, site: Path
) -> None:
    archive = _bundle(tmp_path, name="probe_pkg", body="VALUE = 'first'")
    runtime_install.install(
        root=root, archive=archive, version=_V1, requires=[], site_packages=site
    )
    archive.unlink()  # a no-op install must not read it

    outcome = runtime_install.install(
        root=root, archive=archive, version=_V1, requires=[], site_packages=site
    )

    assert outcome["installed"] is False


def test_an_upgrade_moves_current_and_keeps_the_previous_version(
    tmp_path: Path, root: Path, site: Path
) -> None:
    """Rollback should be a symlink flip, not another upload."""
    first = _bundle(tmp_path, name="probe_pkg", body="VALUE = 'first'")
    second = _bundle(tmp_path, name="probe_pkg2", body="VALUE = 'second'")
    runtime_install.install(
        root=root, archive=first, version=_V1, requires=[], site_packages=site
    )

    runtime_install.install(
        root=root, archive=second, version=_V2, requires=[], site_packages=site
    )

    assert runtime_install.probe(root, site_packages=site)["version"] == _V2
    assert (root / f"sha256-{'1' * 64}").is_dir(), "the previous version was pruned"
    assert (root / f"sha256-{'2' * 64}").is_dir()


def test_a_bundle_that_cannot_import_never_becomes_current(
    tmp_path: Path, root: Path, site: Path
) -> None:
    """The invariant this module exists for.

    A tree that unpacked fine but cannot import is the one failure that would
    otherwise be invisible until an agent's first tool call.
    """
    good = _bundle(tmp_path, name="probe_pkg", body="VALUE = 'first'")
    runtime_install.install(
        root=root, archive=good, version=_V1, requires=["probe_pkg"], site_packages=site
    )
    broken = _bundle(tmp_path, name="probe_pkg2", body="raise ImportError('nope')")

    with pytest.raises(SystemExit):
        runtime_install.install(
            root=root,
            archive=broken,
            version=_V2,
            requires=["probe_pkg2"],
            site_packages=site,
        )

    assert runtime_install.probe(root, site_packages=site)["version"] == _V1


def test_a_second_installer_of_the_same_version_succeeds(
    tmp_path: Path, root: Path, site: Path
) -> None:
    """Two installers racing on one digest write byte-identical trees.

    That is what makes a lock unnecessary, so it has to hold: the loser of the
    rename adopts the winner's directory instead of failing.
    """
    archive = _bundle(tmp_path, name="probe_pkg", body="VALUE = 'first'")
    target = runtime_install._version_directory(root, _V1)
    target.mkdir(parents=True)
    (target / runtime_install.STAMP_NAME).write_text(_V1, encoding="utf-8")
    (target / "site-packages").mkdir()

    outcome = runtime_install.install(
        root=root, archive=archive, version=_V1, requires=[], site_packages=site
    )

    assert outcome["installed"] is True
    assert runtime_install.probe(root, site_packages=site)["version"] == _V1


def test_a_bundle_entry_cannot_escape_the_directory_it_unpacks_into(
    tmp_path: Path, root: Path, site: Path
) -> None:
    """This runs as root, so a traversing archive would write anywhere."""
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../../escaped.txt", "pwned")

    with pytest.raises(SystemExit):
        runtime_install.install(
            root=root, archive=archive, version=_V1, requires=[], site_packages=site
        )

    assert not (tmp_path / "escaped.txt").exists()


def test_an_abandoned_staging_directory_is_swept(
    tmp_path: Path, root: Path, site: Path
) -> None:
    """An install killed mid-unpack leaves one behind, and it is pure waste."""
    archive = _bundle(tmp_path, name="probe_pkg", body="VALUE = 'first'")
    root.mkdir(parents=True)
    abandoned = root / ".incoming-99999"
    abandoned.mkdir()
    (abandoned / "junk").write_text("half a bundle", encoding="utf-8")

    runtime_install.install(
        root=root, archive=archive, version=_V1, requires=[], site_packages=site
    )

    assert not abandoned.exists()


def test_the_overlay_is_placed_ahead_of_the_baked_environment(
    tmp_path: Path, root: Path, site: Path
) -> None:
    """The whole design rests on ``.pth`` ordering, and it is invisible.

    ``lemma-runtime-overlay.pth`` sorts before ``lemma-workspace-overlay.pth``.
    Both insert at position 0, so the *later* one ends up in front -- which is
    what keeps the user's own ``pip install`` tree ahead of ours, and ours ahead
    of the image's. A subprocess is used because that ordering is produced by
    ``site``, not by anything this module can assert directly.
    """
    archive = _bundle(tmp_path, name="probe_pkg", body="VALUE = 'overlay'")
    runtime_install.install(
        root=root, archive=archive, version=_V1, requires=[], site_packages=site
    )
    # The baked copy of the same package, as the image would carry it.
    (site / "probe_pkg").mkdir()
    (site / "probe_pkg" / "__init__.py").write_text("VALUE = 'baked'", encoding="utf-8")
    # And the user's own install, which must stay in front of both.
    user_tree = tmp_path / "user"
    (user_tree / "probe_pkg").mkdir(parents=True)
    (user_tree / "probe_pkg" / "__init__.py").write_text(
        "VALUE = 'user'", encoding="utf-8"
    )
    (site / "lemma-workspace-overlay.pth").write_text(
        f'import sys; p="{user_tree}"; '
        "sys.path.insert(0, p) if p not in sys.path else None\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                f"import site; site.addsitedir({str(site)!r}); "
                "import probe_pkg; print(probe_pkg.VALUE)"
            ),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "user"


def test_the_overlay_wins_when_the_user_has_installed_nothing(
    tmp_path: Path, root: Path, site: Path
) -> None:
    archive = _bundle(tmp_path, name="probe_pkg", body="VALUE = 'overlay'")
    runtime_install.install(
        root=root, archive=archive, version=_V1, requires=[], site_packages=site
    )
    (site / "probe_pkg").mkdir()
    (site / "probe_pkg" / "__init__.py").write_text("VALUE = 'baked'", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                f"import site; site.addsitedir({str(site)!r}); "
                "import probe_pkg; print(probe_pkg.VALUE)"
            ),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "overlay"


def test_the_pth_names_the_symlink_not_the_version(
    tmp_path: Path, root: Path, site: Path
) -> None:
    """So an upgrade never has to rewrite it, and can never half-rewrite it."""
    archive = _bundle(tmp_path, name="probe_pkg", body="VALUE = 'first'")

    runtime_install.install(
        root=root, archive=archive, version=_V1, requires=[], site_packages=site
    )

    written = (site / runtime_install.PTH_NAME).read_text(encoding="utf-8")
    assert "/current/site-packages" in written
    assert "sha256-" not in written


def test_a_version_that_is_not_a_digest_is_refused(root: Path, tmp_path: Path) -> None:
    """The version names a directory, so it must not be able to name a path."""
    with pytest.raises(SystemExit):
        runtime_install._version_directory(root, "../../etc")
