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

import os
import subprocess
import sys
import time
import hashlib
import zipfile
from pathlib import Path

import pytest

from sandbox_runtime import runtime_install


def _bundle(
    tmp_path: Path, *, name: str, body: str, marker: str = ""
) -> tuple[Path, str]:
    """A minimal archive shaped like a real one, and the version naming it.

    The version is returned rather than chosen by the caller because it *is* the
    digest of these bytes -- the installer verifies that now, so a fixture free
    to declare any version would be exercising a system nobody ships. It is
    returned at build time because a successful install consumes the archive.
    """
    archive = tmp_path / f"{name}.zip"
    entries = [(f"site-packages/{name}/__init__.py", body)]
    if marker:
        entries.append((f"site-packages/{marker}", ""))
    with zipfile.ZipFile(archive, "w") as bundle:
        for entry, content in entries:
            bundle.writestr(entry, content)
    # A digest of the *contents*, like the real builder's, and deliberately not
    # of the zip file. Making them the same number here would let a check
    # against the wrong one pass in tests and fail against every bundle ever
    # built -- which is what happened.
    digest = hashlib.sha256()
    for entry, content in entries:
        digest.update(entry.encode())
        digest.update(b"\0")
        digest.update(content.encode())
    return archive, "sha256:" + digest.hexdigest()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "lemma-runtime"


@pytest.fixture
def site(tmp_path: Path) -> Path:
    directory = tmp_path / "site-packages"
    directory.mkdir()
    return directory


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
    archive, archive_version = _bundle(
        tmp_path, name="probe_pkg", body="VALUE = 'first'"
    )

    outcome = runtime_install.install(
        root=root,
        archive=archive,
        version=archive_version,
        requires=["probe_pkg"],
        site_packages=site,
    )

    assert outcome == {"version": archive_version, "installed": True}
    assert runtime_install.probe(root, site_packages=site)["version"] == archive_version
    assert (root / "current" / "site-packages" / "probe_pkg").is_dir()


def test_installing_the_same_version_again_does_no_work(
    tmp_path: Path, root: Path, site: Path
) -> None:
    archive, archive_version = _bundle(
        tmp_path, name="probe_pkg", body="VALUE = 'first'"
    )
    runtime_install.install(
        root=root,
        archive=archive,
        version=archive_version,
        requires=[],
        site_packages=site,
    )
    # A successful install consumes the archive. Left behind, a superseded copy
    # of every bundle the sandbox was ever sent rides into every later snapshot
    # -- and where the sandbox is the disk, that is the user's disk it rides on.
    assert not archive.exists()

    outcome = runtime_install.install(
        root=root,
        archive=archive,
        version=archive_version,
        requires=[],
        site_packages=site,
    )

    assert outcome["installed"] is False, "a no-op install must not need the archive"


def test_an_upgrade_moves_current_and_keeps_the_previous_version(
    tmp_path: Path, root: Path, site: Path
) -> None:
    """Rollback should be a symlink flip, not another upload."""
    first, first_version = _bundle(tmp_path, name="probe_pkg", body="VALUE = 'first'")
    second, second_version = _bundle(
        tmp_path, name="probe_pkg2", body="VALUE = 'second'"
    )
    runtime_install.install(
        root=root, archive=first, version=first_version, requires=[], site_packages=site
    )

    runtime_install.install(
        root=root,
        archive=second,
        version=second_version,
        requires=[],
        site_packages=site,
    )

    assert runtime_install.probe(root, site_packages=site)["version"] == second_version
    assert (root / first_version.replace(":", "-")).is_dir(), (
        "the previous version was pruned"
    )
    assert (root / second_version.replace(":", "-")).is_dir()


def test_a_bundle_that_cannot_import_never_becomes_current(
    tmp_path: Path, root: Path, site: Path
) -> None:
    """The invariant this module exists for.

    A tree that unpacked fine but cannot import is the one failure that would
    otherwise be invisible until an agent's first tool call.
    """
    good, good_version = _bundle(tmp_path, name="probe_pkg", body="VALUE = 'first'")
    runtime_install.install(
        root=root,
        archive=good,
        version=good_version,
        requires=["probe_pkg"],
        site_packages=site,
    )
    broken, broken_version = _bundle(
        tmp_path, name="probe_pkg2", body="raise ImportError('nope')"
    )

    with pytest.raises(SystemExit):
        runtime_install.install(
            root=root,
            archive=broken,
            version=broken_version,
            requires=["probe_pkg2"],
            site_packages=site,
        )

    assert runtime_install.probe(root, site_packages=site)["version"] == good_version


def test_a_second_installer_of_the_same_version_succeeds(
    tmp_path: Path, root: Path, site: Path
) -> None:
    """Two installers racing on one digest write byte-identical trees.

    That is what makes a lock unnecessary, so it has to hold: the loser of the
    rename adopts the winner's directory instead of failing.
    """
    archive, archive_version = _bundle(
        tmp_path, name="probe_pkg", body="VALUE = 'first'"
    )
    target = runtime_install._version_directory(root, archive_version)
    target.mkdir(parents=True)
    (target / runtime_install.STAMP_NAME).write_text(archive_version, encoding="utf-8")
    (target / "site-packages").mkdir()

    outcome = runtime_install.install(
        root=root,
        archive=archive,
        version=archive_version,
        requires=[],
        site_packages=site,
    )

    assert outcome["installed"] is True
    assert runtime_install.probe(root, site_packages=site)["version"] == archive_version


def test_a_bundle_entry_cannot_escape_the_directory_it_unpacks_into(
    tmp_path: Path, root: Path, site: Path
) -> None:
    """This runs as root, so a traversing archive would write anywhere."""
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../../escaped.txt", "pwned")
    version = "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()

    with pytest.raises(SystemExit):
        runtime_install.install(
            root=root,
            archive=archive,
            version=version,
            requires=[],
            site_packages=site,
        )

    assert not (tmp_path / "escaped.txt").exists()


def test_a_staging_directory_left_behind_long_ago_is_swept(
    tmp_path: Path, root: Path, site: Path
) -> None:
    """An install killed mid-unpack leaves one behind, and it is pure waste."""
    archive, archive_version = _bundle(
        tmp_path, name="probe_pkg", body="VALUE = 'first'"
    )
    root.mkdir(parents=True)
    abandoned = root / ".incoming-99999"
    abandoned.mkdir()
    (abandoned / "junk").write_text("half a bundle", encoding="utf-8")
    stale = time.time() - runtime_install._ABANDONED_STAGING_SECONDS - 60
    os.utime(abandoned, (stale, stale))

    runtime_install.install(
        root=root,
        archive=archive,
        version=archive_version,
        requires=[],
        site_packages=site,
    )

    assert not abandoned.exists()


def test_a_staging_directory_another_installer_is_using_is_left_alone(
    tmp_path: Path, root: Path, site: Path
) -> None:
    """Racing installers cost a wasted copy, never a failed one.

    Every installer stages into its own `.incoming-<pid>`. Sweeping all of them
    meant the second to arrive deleted the first's tree out from under it,
    mid-extract -- turning the lock-free design into a way for two installs to
    break each other.
    """
    archive, archive_version = _bundle(
        tmp_path, name="probe_pkg", body="VALUE = 'first'"
    )
    root.mkdir(parents=True)
    live = root / ".incoming-12345"
    live.mkdir()
    (live / "partial").write_text("still being written", encoding="utf-8")

    runtime_install.install(
        root=root,
        archive=archive,
        version=archive_version,
        requires=[],
        site_packages=site,
    )

    assert live.exists(), "another installer's staging directory was deleted"


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
    archive, archive_version = _bundle(
        tmp_path, name="probe_pkg", body="VALUE = 'overlay'"
    )
    runtime_install.install(
        root=root,
        archive=archive,
        version=archive_version,
        requires=[],
        site_packages=site,
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
    archive, archive_version = _bundle(
        tmp_path, name="probe_pkg", body="VALUE = 'overlay'"
    )
    runtime_install.install(
        root=root,
        archive=archive,
        version=archive_version,
        requires=[],
        site_packages=site,
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
    archive, archive_version = _bundle(
        tmp_path, name="probe_pkg", body="VALUE = 'first'"
    )

    runtime_install.install(
        root=root,
        archive=archive,
        version=archive_version,
        requires=[],
        site_packages=site,
    )

    written = (site / runtime_install.PTH_NAME).read_text(encoding="utf-8")
    assert "/current/site-packages" in written
    assert "sha256-" not in written


def test_an_archive_corrupted_in_transit_is_refused(
    tmp_path: Path, root: Path, site: Path
) -> None:
    """Verified against the file digest, which is not the version.

    The version digests the unpacked *contents* -- paths, bytes and modes -- so
    rebuilding the zip does not invent a new identity. The archive digest is of
    the bytes on disk. Checking the archive against the version compares two
    numbers that are never equal, which is how this was written first: it
    refused every real bundle, and only installing one showed it.
    """
    archive, version = _bundle(tmp_path, name="probe_pkg", body="VALUE = 'overlay'")
    sha = "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.write_bytes(archive.read_bytes() + b"corrupted")

    with pytest.raises(SystemExit, match="does not match the expected"):
        runtime_install.install(
            root=root,
            archive=archive,
            version=version,
            requires=["probe_pkg"],
            archive_sha256=sha,
            site_packages=site,
        )

    assert runtime_install.probe(root, site_packages=site)["version"] is None
    # Kept, not consumed: a retry has something to retry against.
    assert archive.exists()


def test_an_intact_archive_passes_its_digest_check(
    tmp_path: Path, root: Path, site: Path
) -> None:
    """The half the corruption test cannot prove alone.

    A verifier that rejected everything satisfies that one -- and the first
    version of this check did exactly that, against every bundle ever built.
    """
    archive, version = _bundle(tmp_path, name="probe_pkg", body="VALUE = 'overlay'")
    sha = "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()

    outcome = runtime_install.install(
        root=root,
        archive=archive,
        version=version,
        requires=["probe_pkg"],
        archive_sha256=sha,
        site_packages=site,
    )

    assert outcome == {"version": version, "installed": True}
    assert runtime_install.probe(root, site_packages=site)["version"] == version


def test_nothing_is_published_before_it_is_wired(
    tmp_path: Path, root: Path, site: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sandbox must never answer "installed" for a tree nothing imports from.

    The stamp reachable through `current` *is* the claim -- the backend reads
    that exact file -- so if `current` moves before the `.pth` is written, a
    process dying in between leaves the claim true forever while the sandbox
    serves the baked copy. Unrecoverable, because every later ensure believes
    the overlay is already there.

    Simulated by failing the flip: with the order correct the wiring is already
    on disk when that happens.
    """
    archive, version = _bundle(tmp_path, name="probe_pkg", body="VALUE = 'overlay'")

    def _die(*_args: object, **_kwargs: object) -> None:
        raise SystemExit("killed while publishing")

    monkeypatch.setattr(runtime_install, "_point_current_at", _die)

    with pytest.raises(SystemExit):
        runtime_install.install(
            root=root,
            archive=archive,
            version=version,
            requires=["probe_pkg"],
            site_packages=site,
        )

    assert (site / runtime_install.PTH_NAME).is_file(), (
        "the overlay was published before it was wired"
    )


def test_an_archive_for_a_version_already_installed_is_not_left_behind(
    tmp_path: Path, root: Path, site: Path
) -> None:
    """Reaching the already-current path means two installers raced.

    The loser is holding a copy of a bundle that is already installed, and on a
    fabric where the sandbox is the disk anything left in the staging area rides
    into every later snapshot of a disk the user owns.
    """
    archive, version = _bundle(tmp_path, name="probe_pkg", body="VALUE = 'overlay'")
    runtime_install.install(
        root=root,
        archive=archive,
        version=version,
        requires=["probe_pkg"],
        site_packages=site,
    )
    again, _ = _bundle(tmp_path, name="probe_pkg", body="VALUE = 'overlay'")

    outcome = runtime_install.install(
        root=root,
        archive=again,
        version=version,
        requires=["probe_pkg"],
        site_packages=site,
    )

    assert outcome["installed"] is False
    assert not again.exists(), "a redundant archive was left on the user's disk"


def test_a_sibling_staging_directory_is_not_inside_this_one(
    tmp_path: Path, root: Path, site: Path
) -> None:
    """Containment is a path relationship, not a string prefix.

    Staging directories are siblings named `.incoming-<pid>`, so a prefix test
    rooted at `.incoming-12` accepts every path under `.incoming-127`. The
    sibling therefore has to extend *this* installer's own name, or the prefix
    test refuses it for the wrong reason and the test proves nothing.
    """
    archive = tmp_path / "sibling.zip"
    sibling = f".incoming-{os.getpid()}9"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(f"../{sibling}/stowaway.txt", "escaped")
    version = "sha256:" + hashlib.sha256(b"sibling").hexdigest()

    with pytest.raises(SystemExit, match="escapes its directory"):
        runtime_install.install(
            root=root,
            archive=archive,
            version=version,
            requires=[],
            site_packages=site,
        )


def test_an_abandoned_current_symlink_is_swept(
    tmp_path: Path, root: Path, site: Path
) -> None:
    """`_point_current_at` stages a symlink before `os.replace` moves it.

    A process killed between those two leaves one behind, and on a fabric where
    the sandbox is the disk it is there forever. Two details make sweeping it
    different from sweeping a staging directory: `stat` follows the link and
    reports the age of the version it points at, which stays young for as long
    as that version is current; and `rmtree` refuses a symlink, silently so
    under `ignore_errors`.
    """
    archive, version = _bundle(tmp_path, name="probe_pkg", body="VALUE = 'overlay'")
    runtime_install.install(
        root=root,
        archive=archive,
        version=version,
        requires=["probe_pkg"],
        site_packages=site,
    )
    stranded = root / ".current-999999"
    stranded.symlink_to(runtime_install._version_directory(root, version).name)
    old = time.time() - (runtime_install._ABANDONED_STAGING_SECONDS + 60)
    os.utime(stranded, (old, old), follow_symlinks=False)

    runtime_install._prune(root, keep=runtime_install._version_directory(root, version))

    assert not stranded.is_symlink(), "the stranded staging symlink was not swept"
    assert (root / runtime_install.CURRENT_LINK).is_symlink(), "current was swept"


def test_a_version_that_is_not_a_digest_is_refused(root: Path, tmp_path: Path) -> None:
    """The version names a directory, so it must not be able to name a path."""
    with pytest.raises(SystemExit):
        runtime_install._version_directory(root, "../../etc")


def test_a_module_that_imports_from_outside_the_overlay_is_not_accepted(
    tmp_path: Path, root: Path, site: Path
) -> None:
    """Importing is not the same as importing *what was just installed*.

    The image carries its own copy of everything the bundle ships, so a bundle
    that had dropped a package would still import cleanly -- from the baked copy
    -- and `current` would move behind it, leaving the sandbox reporting a
    version it is not running. That is the one failure this mechanism exists to
    make impossible.

    `json` stands in for the baked copy: it imports anywhere and comes from
    nowhere near the overlay, which is exactly the shape of the problem.
    """
    archive, archive_version = _bundle(
        tmp_path, name="probe_pkg", body="VALUE = 'overlay'"
    )

    with pytest.raises(SystemExit, match="not from the overlay"):
        runtime_install.install(
            root=root,
            archive=archive,
            version=archive_version,
            requires=["probe_pkg", "json"],
            site_packages=site,
        )

    assert runtime_install.probe(root, site_packages=site)["version"] is None
