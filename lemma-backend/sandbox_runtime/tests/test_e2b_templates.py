from __future__ import annotations

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

    def __init__(self, **_: object) -> None:
        self.copies = []
        self.commands = []

    def __getattr__(self, name: str):
        def record(*args: object, **_: object) -> _RecordingTemplate:
            if name == "copy":
                self.copies.append((str(args[0]), str(args[1])))
            if name == "run_cmd" and args:
                self.commands.append(str(args[0]))
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
