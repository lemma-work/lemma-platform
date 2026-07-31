from __future__ import annotations

from scripts.build_local_host_pack import npm_executable


def test_uses_windows_command_shim_for_npm() -> None:
    assert npm_executable("nt") == "npm.cmd"


def test_uses_direct_npm_executable_on_posix() -> None:
    assert npm_executable("posix") == "npm"
