from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


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

    def __init__(self, **_: object) -> None:
        self.copies = []

    def __getattr__(self, name: str):
        def record(*args: object, **_: object) -> _RecordingTemplate:
            if name == "copy":
                self.copies.append((str(args[0]), str(args[1])))
            return self

        return record


def test_workspace_template_includes_cli_skill_sources(monkeypatch) -> None:
    monkeypatch.setattr(build_templates, "Template", _RecordingTemplate)

    template = build_templates.workspace_template()

    assert ("lemma-cli", "/build/lemma-cli") in template.copies
    assert ("lemma-skills", "/build/lemma-skills") in template.copies
