from __future__ import annotations

import re
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


# The builder these exercise imports the E2B SDK, which is an optional extra:
# a Docker-only deployment installs neither, and the unit job deliberately
# does not either, so that the provider's SDK seam stays honest. The E2B
# conformance workflow installs it and runs these.
pytest.importorskip("e2b")

_MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "sandbox-images"
    / "templates"
    / "e2b"
    / "build_templates.py"
)
_SPEC = spec_from_file_location("e2b_build_templates", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
build_templates = module_from_spec(_SPEC)
_SPEC.loader.exec_module(build_templates)


class _RecordingTemplate:
    copies: list[tuple[str, str]]
    commands: list[str]
    #: Every builder step in order, as `(method, first argument, user)`, so a
    #: test can ask what ran *before* something else was in place.
    steps: list[tuple[str, str, str | None]]
    ready_commands: list[str]
    start_commands: list[str]

    def __init__(self, **_: object) -> None:
        self.copies = []
        self.commands = []
        self.steps = []
        self.ready_commands = []
        self.start_commands = []

    def __getattr__(self, name: str):
        def record(*args: object, **kwargs: object) -> _RecordingTemplate:
            user = kwargs.get("user")
            self.steps.append(
                (name, str(args[0]) if args else "", str(user) if user else None)
            )
            if name == "copy":
                self.copies.append((str(args[0]), str(args[1])))
            if name == "run_cmd" and args:
                self.commands.append(str(args[0]))
            if name == "set_ready_cmd":
                self.ready_commands.append(str(args[0]))
            if name == "set_start_cmd":
                self.start_commands.append(str(args[0]))
            return self

        return record


def test_workspace_template_includes_cli_skill_sources(monkeypatch) -> None:
    monkeypatch.setattr(build_templates, "Template", _RecordingTemplate)

    template = build_templates.workspace_template()

    assert ("lemma-cli", "/build/lemma-cli") in template.copies
    assert ("lemma-skills", "/build/lemma-skills") in template.copies


def test_the_workspace_template_bakes_somewhere_for_the_overlay_to_land(
    monkeypatch,
) -> None:
    """The backend installs first-party code into a running sandbox, and this is
    what lets it do so without reaching root.

    `/opt` is root-owned, so an unbaked overlay directory means the install has
    to elevate -- which works on E2B only because its user happens to have
    passwordless sudo, and is exactly the kind of accident that stops being true
    without warning. Creating it here, owned by the sandbox user, makes the
    install an ordinary write.
    """
    monkeypatch.setattr(build_templates, "Template", _RecordingTemplate)

    template = build_templates.workspace_template()
    script = "\n".join(template.commands)

    assert "mkdir -p /opt/lemma-runtime" in script
    assert "chown user:user /opt/lemma-runtime" in script


def test_the_overlay_is_wired_ahead_of_the_images_own_copy(monkeypatch) -> None:
    """`.pth` ordering is the whole mechanism and it is invisible.

    Both files insert at position 0, so the one processed *later* ends up in
    front. `lemma-runtime-` sorts before `lemma-workspace-`, which is what keeps
    a package the user pip-installed ahead of the overlay, and the overlay ahead
    of the copy baked into this image. Rename either file and the precedence
    silently inverts, so the names are asserted rather than assumed.
    """
    monkeypatch.setattr(build_templates, "Template", _RecordingTemplate)

    template = build_templates.workspace_template()
    script = "\n".join(template.commands)

    assert "lemma-runtime-overlay.pth" in script
    assert "lemma-workspace-overlay.pth" in script
    assert "lemma-runtime-overlay.pth" < "lemma-workspace-overlay.pth"
    # Named for the symlink, so an upgrade is a flip rather than a rewrite.
    assert '/opt/lemma-runtime/current/site-packages"' in script


def test_the_image_still_carries_a_floor_under_the_overlay(monkeypatch) -> None:
    """The baked copy stays, deliberately.

    It supplies the third-party closure the overlay is installed `--no-deps`
    against, and it is what a sandbox falls back to when an install fails. Its
    being stale is now harmless rather than the problem: the overlay supersedes
    it, so a Lemma code change no longer needs a template published -- which, on
    a provider where the sandbox is the disk, is what made publishing one and
    destroying the fleet the same act.
    """
    monkeypatch.setattr(build_templates, "Template", _RecordingTemplate)

    template = build_templates.workspace_template()

    assert ("lemma-cli", "/build/lemma-cli") in template.copies
    assert ("lemma-python", "/build/lemma-python") in template.copies


_DOCKERFILE = (
    Path(__file__).resolve().parents[2] / "sandbox-images" / "Dockerfile.workspace"
)


def test_both_images_wire_the_overlay_the_same_way(monkeypatch) -> None:
    """The two fabrics must agree about where the overlay goes.

    They have disagreed before, silently and for a long time: `PNPM_HOME` points
    into the durable volume on Docker and into the home directory on E2B, and
    `UV_CACHE_DIR` is set on one and absent on the other. Each divergence was a
    reasonable local decision that nothing compared.

    Here it would be worse than untidy. The Docker lane is the only one CI
    actually runs, so if the overlay is wired differently there, the mechanism
    is covered by nothing and the fabric that carries production is the one
    nobody tested.
    """
    monkeypatch.setattr(build_templates, "Template", _RecordingTemplate)
    template = build_templates.workspace_template()
    e2b_script = "\n".join(template.commands)
    dockerfile = _DOCKERFILE.read_text(encoding="utf-8")

    for fragment in (
        "lemma-runtime-overlay.pth",
        '/opt/lemma-runtime/current/site-packages"',
    ):
        assert fragment in e2b_script, f"the E2B template lost {fragment}"
        assert fragment in dockerfile, f"Dockerfile.workspace lost {fragment}"
    assert "/opt/lemma-runtime" in dockerfile


def _bare_uv_calls(command: str) -> list[str]:
    """`uv` invoked by that name, as opposed to by a path or inside a word.

    Matches `uv sync`, `uv cache clean`, `&& uv python ...` -- the name on PATH,
    which is the `lemma-uv` shim -- and not `/usr/local/lib/lemma-uv-bin sync`,
    `uvx` or `UV_CACHE_DIR`.
    """
    return re.findall(r"(?:^|[\s;&|(])uv\s+\w+", command)


def test_no_build_step_calls_uv_before_the_shim_exists(monkeypatch) -> None:
    """The workspace template could not be built from scratch.

    `_install_uv_command` installs the real binary as
    `/usr/local/lib/lemma-uv-bin`, and the name `uv` on PATH is the `lemma-uv`
    shim, copied in near the end. The Python step between them ran a bare
    `uv python install` / `uv sync`, which is `command not found` (exit 127)
    on a cold build -- hidden for as long as every build hit a warm layer
    cache. The function template has no shim at all, so the same holds there.
    """
    monkeypatch.setattr(build_templates, "Template", _RecordingTemplate)

    for factory in (
        build_templates.workspace_template,
        build_templates.workspace_docker_template,
        build_templates.function_template,
    ):
        template = factory()
        shim_at = next(
            (
                index
                for index, (method, source, _) in enumerate(template.steps)
                if method == "copy" and source.endswith("scripts/lemma-uv")
            ),
            len(template.steps),
        )
        before_shim = [
            source
            for method, source, _ in template.steps[:shim_at]
            if method == "run_cmd"
        ]
        offenders = [call for c in before_shim for call in _bare_uv_calls(c)]
        assert not offenders, f"{factory.__name__} calls bare uv: {offenders}"


def test_the_python_step_calls_uv_by_its_real_path(monkeypatch) -> None:
    monkeypatch.setattr(build_templates, "Template", _RecordingTemplate)

    script = "\n".join(build_templates.workspace_template().commands)

    assert build_templates.UV == "/usr/local/lib/lemma-uv-bin"
    assert (
        f"install -m 0755 /tmp/uv-x86_64-unknown-linux-gnu/uv {build_templates.UV}"
        in script
    )
    assert f"{build_templates.UV} python install" in script
    assert f"{build_templates.UV} sync --project" in script
    assert f"{build_templates.UV} cache clean" in script


def _docker_steps(monkeypatch) -> tuple[_RecordingTemplate, str]:
    monkeypatch.setattr(build_templates, "Template", _RecordingTemplate)
    template = build_templates.workspace_docker_template()
    workspace_steps = len(build_templates.workspace_template().steps)
    return template, "\n".join(
        source
        for method, source, _ in template.steps[workspace_steps:]
        if method == "run_cmd"
    )


def test_the_docker_template_is_the_workspace_with_docker_on_top(
    monkeypatch,
) -> None:
    """Everything the workspace bakes, unchanged, then the Docker layers -- so
    the two share a layer cache and a machine on either has the same tools."""
    monkeypatch.setattr(build_templates, "Template", _RecordingTemplate)
    workspace = build_templates.workspace_template()
    docker = build_templates.workspace_docker_template()

    assert docker.steps[: len(workspace.steps)] == workspace.steps
    added = docker.steps[len(workspace.steps) :]
    assert [method for method, _, _ in added] == ["run_cmd", "set_ready_cmd"]
    assert added[0][2] == "root"


def test_the_docker_apt_repository_is_debian(monkeypatch) -> None:
    """`code-interpreter-v1` is Debian 13 (trixie), not Ubuntu. Docker's
    `linux/ubuntu` suite has no `trixie`, so the wrong one fails `apt-get
    update`; the codename comes from the image rather than being pinned."""
    _, script = _docker_steps(monkeypatch)

    assert "https://download.docker.com/linux/debian/gpg" in script
    assert (
        "https://download.docker.com/linux/debian ${VERSION_CODENAME} stable" in script
    )
    assert ". /etc/os-release" in script
    assert "linux/ubuntu" not in script
    for package in (
        "docker-ce",
        "docker-ce-cli",
        "containerd.io",
        "docker-buildx-plugin",
        "docker-compose-plugin",
        "iptables",
    ):
        assert f" {package}" in script, package


def test_the_sandbox_user_runs_docker_without_sudo(monkeypatch) -> None:
    _, script = _docker_steps(monkeypatch)

    assert "usermod -aG docker user" in script


def test_the_docker_template_is_ready_on_docker_info_with_no_start_command(
    monkeypatch,
) -> None:
    """systemd starts `docker.service` at boot and on every filesystem-only
    resume, so a start command would only race it. Ready means the daemon
    answers."""
    template, _ = _docker_steps(monkeypatch)

    assert template.ready_commands == [build_templates.DOCKER_READY_COMMAND]
    assert "docker info" in build_templates.DOCKER_READY_COMMAND
    assert template.start_commands == []
