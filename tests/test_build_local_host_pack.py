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
    WINDOWS_PATH_BUDGET,
    _resolve_rpath_libraries,
    enforce_windows_path_budget,
    prune_unused_twilio_domains,
    report_windows_path_headroom,
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


def _pack_with(root: Path, relative: str, body: bytes = b"x") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


def test_bytecode_windows_cannot_open_is_dropped_rather_than_shipped(tmp_path, capsys):
    """1,349 files landed past Windows' 260-character limit on a real install.

    They were written -- Rust addresses files with the extended-length prefix
    and is not bound by
    the limit -- and then could not be opened by anything that is not, which
    includes the pack's own Python. The backend failed to import a module that
    `Get-ChildItem` was listing in the same directory, and the app could not
    start. Nothing before this noticed, because the pack had never been
    installed on Windows.
    """
    pack = tmp_path / "pack"
    deep = "backend/python/Lib/site-packages/" + "/".join(["seg"] * 30)
    over = _pack_with(pack, f"{deep}/__pycache__/module.cpython-314.pyc")
    within = _pack_with(pack, "backend/python/Lib/site-packages/__pycache__/a.pyc")
    source = _pack_with(pack, "backend/python/Lib/site-packages/a.py")

    enforce_windows_path_budget(pack)

    assert not over.exists(), "bytecode past the budget cannot be read once installed"
    assert within.exists(), "the other forty-four thousand keep their cache"
    assert source.exists()
    assert "dropped 1 cached bytecode" in capsys.readouterr().out


def test_a_source_file_past_the_budget_fails_the_build(tmp_path):
    """Bytecode can be dropped; source cannot.

    A `.py` that Windows cannot open is a pack that works on one platform and
    is broken on the other, with nothing to say so until someone installs it.
    """
    pack = tmp_path / "pack"
    _pack_with(pack, "backend/" + "/".join(["segment"] * 30) + "/module.py")

    with pytest.raises(SystemExit) as failure:
        enforce_windows_path_budget(pack)

    assert "Windows cannot open them" in str(failure.value)


def test_the_budget_leaves_room_for_where_the_pack_is_installed():
    """The arithmetic this number comes from, so it cannot drift silently.

    An installed pack sits under
    `%LOCALAPPDATA%\\Lemma\\runtime\\releases\\<version>-<8 hex>\\`.
    """
    longest_user_name = 20
    release_directory = (
        len("C:\\Users\\")
        + longest_user_name
        + len("\\AppData\\Local\\Lemma\\runtime\\releases\\")
        + len("0.7.2-abcdef12")
    )
    usable = 259  # MAX_PATH counts the terminating NUL.
    assert release_directory + 1 + WINDOWS_PATH_BUDGET <= usable, (
        "the budget has to fit under the directory the pack is installed into"
    )


def test_twilio_keeps_its_client_and_loses_the_api_nobody_calls(tmp_path: Path) -> None:
    """Twilio is here by accident of the dependency graph.

    Nothing in `app` imports it. `supertokens_python` does, at module level, so
    that its passwordless recipe can send a code by SMS -- a feature this
    product does not enable. So the package has to stay importable while the
    REST tree it never walks does not: that tree is where a source file sat
    past the Windows path budget, which fails the build rather than shipping a
    pack whose backend cannot import a module in its own directory listing.
    """
    site_packages = tmp_path / "site-packages"
    rest = site_packages / "twilio" / "rest"
    (rest / "api" / "v2010" / "account" / "sip").mkdir(parents=True)
    (rest / "api" / "v2010" / "account" / "sip" / "domain.py").write_text("x")
    (rest / "messaging").mkdir()
    (rest / "__init__.py").write_text("from twilio.base.client_base import ClientBase")
    (site_packages / "twilio" / "base").mkdir()
    (site_packages / "twilio" / "base" / "client_base.py").write_text("class ClientBase: ...")
    (site_packages / "twilio" / "__init__.py").write_text("")

    prune_unused_twilio_domains(site_packages)

    assert (rest / "__init__.py").is_file(), "the module supertokens imports has to survive"
    assert (site_packages / "twilio" / "base" / "client_base.py").is_file(), (
        "and everything it imports at module level with it"
    )
    assert not (rest / "api").exists(), "the domain holding the over-budget path is gone"
    assert not (rest / "messaging").exists(), "and so are its siblings; none are reachable"


def test_pruning_twilio_is_safe_where_twilio_is_absent(tmp_path: Path) -> None:
    """A pack built without it is not a pack that fails to build."""
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    prune_unused_twilio_domains(site_packages)


def test_a_pack_says_how_much_windows_path_budget_is_left(tmp_path, capsys) -> None:
    """The gate only speaks once something has crossed the line.

    Which makes every crossing a surprise: a build that was fine yesterday
    fails today because a dependency grew a directory level. A `twilio` file
    sat nine characters over, and nothing before it had ever said how much room
    was left.
    """
    # Under the pack root directly: `installed_path` is what prepends
    # `local-runtime/`, so building one here would count it twice.
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "short.py").write_text("x")

    report_windows_path_headroom(pack)

    printed = capsys.readouterr().out
    assert "longest installed path is" in printed
    assert "below the 170-character Windows budget" in printed
    assert "::warning::" not in printed, "a short path is not worth a warning"


def test_a_pack_close_to_the_limit_says_so_before_it_fails(tmp_path, capsys) -> None:
    """The last quiet release before a failure should not look like the rest."""
    pack = tmp_path / "pack"
    deep = pack / ("d" * 120)
    deep.mkdir(parents=True)
    # Inside the budget, and only just. `installed_path` adds the
    # `local-runtime/` prefix, so that is counted here rather than created.
    prefix = len("local-runtime/") + 120 + 1
    name = "n" * (WINDOWS_PATH_BUDGET - prefix - len(".py"))
    (deep / f"{name}.py").write_text("x")

    report_windows_path_headroom(pack)

    printed = capsys.readouterr().out
    assert "::warning::" in printed, printed
    assert "will fail the build" in printed
