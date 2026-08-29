"""The producer half of the host-pack layout contract.

This file used to be two assertions about `npm_executable`, for a script that
lays out a 500 MB artifact the desktop app then hard-codes a dozen paths into.
Neither side had any idea what the other did, and neither could: a PR runs the
consumer's tests against a fixture the same PR wrote, while the pack itself is
built by a release job on a different trigger. A rename lands green on both
sides and is found by whoever installs the release.

The Rust half is
`native_host_pack.rs::the_packaged_layout_matches_the_committed_contract`, and
both assert against `desktop/contracts/host-pack-layout.json`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.build_local_host_pack import (
    _resolve_rpath_libraries,
    copy_browser_assets,
    copy_node_runtime,
    npm_executable,
    standalone_server,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads(
    (REPO_ROOT / "desktop" / "contracts" / "host-pack-layout.json").read_text()
)
BUILDER = (REPO_ROOT / "scripts" / "build_local_host_pack.py").read_text()


def test_uses_windows_command_shim_for_npm() -> None:
    assert npm_executable("nt") == "npm.cmd"


def test_uses_direct_npm_executable_on_posix() -> None:
    assert npm_executable("posix") == "npm"


def test_the_builder_writes_every_path_the_app_looks_for() -> None:
    """Each contract path appears in the script that is supposed to write it.

    A source assertion rather than a built artifact, deliberately: building one
    needs npm, uv, a Next production build and several minutes, which is not a
    thing to put on every PR. What it catches is the whole failure mode anyway
    -- somebody renames a directory on one side of the contract and not the
    other.

    Entries whose `producer_writes` is null are covered by real tests instead,
    below: the script assembles those paths from pieces, so no substring of it
    would prove anything either way.
    """
    checked = 0
    for entry in CONTRACT["required"]:
        produced = entry["producer_writes"]
        if produced is None:
            continue
        checked += 1
        assert produced in BUILDER, (
            f"the contract says {entry['what']} is written to {produced}, "
            f"which build_local_host_pack.py never mentions"
        )
    assert checked, "every required entry opted out of this check"
    for entry in CONTRACT["derived"]:
        produced = entry["producer_writes"]
        if produced is None:
            continue
        checked += 1
        assert produced in BUILDER, (
            f"the contract names {entry['what']} at {entry['path']}, which "
            f"build_local_host_pack.py never writes"
        )


def test_the_node_binary_lands_where_the_app_starts_it(tmp_path: Path) -> None:
    """The one required path the script assembles rather than spells.

    `copy_node_runtime` joins `frontend / "node" / relative`, so nothing in the
    source reads "frontend/node" and only running it proves where the file
    goes. This is the binary that runs the entire frontend; putting it one
    directory over is an install that downloads half a gigabyte and then cannot
    start.
    """
    fake_node_root = tmp_path / "node-v24"
    (fake_node_root / "bin").mkdir(parents=True)
    fake_node = fake_node_root / "bin" / "node"
    # Executable, because `copy_node_runtime` now starts what it copied.
    fake_node.write_text("#!/bin/sh\nexit 0\n")
    fake_node.chmod(0o755)
    frontend = tmp_path / "pack" / "frontend"

    copy_node_runtime(frontend, fake_node_root)

    landed = {
        f"frontend/{path.relative_to(frontend)}"
        for path in frontend.rglob("*")
        if path.is_file()
    }
    candidates = set(
        next(
            entry["candidates"]
            for entry in CONTRACT["required"]
            if entry["what"] == "frontend Node.js"
        )
    )
    assert landed & candidates, (
        f"node was staged at {sorted(landed)}, and the app looks in "
        f"{sorted(candidates)}"
    )


def test_a_node_that_cannot_start_is_refused_at_build_time(tmp_path: Path) -> None:
    """The failure this pack has no other way of noticing.

    A Node copied out of a build that links `@rpath/libnode.<abi>.dylib` --
    which the GitHub tool-cache build of 22.23.1 does, and Homebrew's does with
    a dozen kegs -- lands as a file of the right name, the right size and the
    right permissions, and dies in dyld the first time the app serves a page.
    Every check that reads the pack rather than running it says the pack is
    fine. So the copy is started, and refused here rather than on a user's
    machine four minutes into a first run.
    """
    fake_node_root = tmp_path / "node-broken"
    (fake_node_root / "bin").mkdir(parents=True)
    fake_node = fake_node_root / "bin" / "node"
    fake_node.write_text("#!/bin/sh\necho 'dyld: Library not loaded' >&2\nexit 6\n")
    fake_node.chmod(0o755)

    with pytest.raises(SystemExit) as refusal:
        copy_node_runtime(tmp_path / "pack" / "frontend", fake_node_root)

    assert "cannot start" in str(refusal.value)


def test_the_libraries_node_carries_an_rpath_to_are_packed_beside_it(
    tmp_path: Path,
) -> None:
    """`bin/node` resolves `@rpath` through `@loader_path/../lib`.

    So a library that has to travel with the executable travels to
    `frontend/node/lib`, not next to the binary and not left behind. Asserted
    on the resolver rather than on a real Mach-O binary, because the whole
    point is that most builds have nothing here and one build did.
    """
    root = tmp_path / "node-shared"
    (root / "bin").mkdir(parents=True)
    (root / "lib").mkdir()
    (root / "bin" / "node").write_text("")
    (root / "lib" / "libnode.127.dylib").write_text("")

    resolved = _resolve_rpath_libraries(
        root, root / "bin" / "node", ["@rpath/libnode.127.dylib", "/usr/lib/libz.1.dylib"]
    )

    assert resolved == [root / "lib" / "libnode.127.dylib"]


def test_the_browser_bundles_land_where_the_backend_serves_them(
    tmp_path: Path,
) -> None:
    """The two files the workspace page loads before it can render anything.

    Written into a directory the script joins, so like the node binary these
    are proved by running it. They come from `lemma-typescript/public/`, which
    is committed, so this needs no build.
    """
    copy_browser_assets(tmp_path)

    landed = {
        str(path.relative_to(tmp_path))
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    for entry in CONTRACT["derived"]:
        if "browser" not in entry["what"]:
            continue
        assert entry["path"] in landed, (
            f"{entry['what']} was staged at {sorted(landed)}, and the app "
            f"reads it from {entry['path']}"
        )


@pytest.mark.parametrize(
    "layout",
    ["server.js", "app/server.js", "lemma-frontend/server.js"],
)
def test_the_server_is_found_wherever_next_decides_to_put_it(
    tmp_path: Path, layout: str
) -> None:
    """Next nests its standalone server under a name it chooses itself.

    Which name has changed with Next versions, so both sides probe three
    candidates in the same order -- and this is the one part of the contract
    with real behaviour behind it rather than a string, so it gets a real test.
    """
    server = tmp_path / layout
    server.parent.mkdir(parents=True, exist_ok=True)
    server.write_text("// next standalone entrypoint\n")

    found = standalone_server(tmp_path)

    assert found == server
    # Whatever it found, the app must be looking there. The pack copies the
    # standalone tree into `frontend/`, so the relative path is what the two
    # sides share.
    expected = f"frontend/{found.relative_to(tmp_path)}"
    candidates = next(
        entry["candidates"]
        for entry in CONTRACT["required"]
        if entry["what"] == "Next.js standalone server"
    )
    assert expected in candidates


def test_the_server_candidates_are_tried_in_the_order_both_sides_agree_on(
    tmp_path: Path,
) -> None:
    """Order is part of the contract, not an implementation detail.

    With more than one present -- which happens, because a stale build leaves
    the previous layout behind -- the two sides must pick the same file. Picking
    differently means the pack ships one server and the app starts the other.
    """
    for layout in ("lemma-frontend/server.js", "app/server.js", "server.js"):
        path = tmp_path / layout
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("// next standalone entrypoint\n")

    candidates = next(
        entry["candidates"]
        for entry in CONTRACT["required"]
        if entry["what"] == "Next.js standalone server"
    )
    first = candidates[0].removeprefix("frontend/")
    assert standalone_server(tmp_path) == tmp_path / first


def test_a_pack_with_no_server_names_what_it_looked_for(tmp_path: Path) -> None:
    # The failure a user sees is a progress bar that stops. Whatever reaches the
    # log has to say which paths were tried, or the next person debugging it has
    # nothing to go on.
    with pytest.raises(SystemExit) as raised:
        standalone_server(tmp_path)
    message = str(raised.value)
    for candidate in ("server.js", "app/server.js", "lemma-frontend/server.js"):
        assert candidate in message
