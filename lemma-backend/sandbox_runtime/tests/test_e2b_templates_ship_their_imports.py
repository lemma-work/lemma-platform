"""Every `sandbox_runtime` module a template ships must be able to import.

The E2B templates are assembled by `build_templates.py`, not by
`Dockerfile.workspace` -- the two are separate definitions of the same idea and
they drift. `test_e2b_template_sources_exist` already checks that each copied
path exists in the repository, which is a different question from whether the
code that lands can actually run: the workspace template copied
`browser_relay/` and `__init__.py` and not `tasks.py`, which
`browser_relay.app` and `browser_relay.stream_proxy` both import. Every
workspace sandbox therefore shipped a relay that raised `ModuleNotFoundError`
on its first line, left no log because it died before logging was configured,
and presented as a browser stuck on "Connecting...".

Resolved statically, from the builder's own copy list, so this needs no SDK, no
network and no template build, and runs on every commit -- unlike the two tests
that `importorskip("e2b")` and only run in the conformance workflow.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
BUILDER = BACKEND / "sandbox-images" / "templates" / "e2b" / "build_templates.py"
PREFIX = "lemma-backend/sandbox_runtime/"


def _copies_per_template() -> dict[str, list[str]]:
    """Each template function, and the `sandbox_runtime` paths its body copies.

    Keyed off the `def ..._template()` boundaries rather than the whole file,
    because the point is that *this* template ships what *its* modules import.
    Both currently copy `tasks.py`; reading them together would let one cover
    for the other.
    """
    text = BUILDER.read_text(encoding="utf-8")
    bounds = [
        (m.group(1), m.start())
        for m in re.finditer(r"^def (\w*template)\(", text, re.MULTILINE)
    ]
    assert bounds, "builder no longer declares template functions"
    per: dict[str, list[str]] = {}
    for index, (name, start) in enumerate(bounds):
        end = bounds[index + 1][1] if index + 1 < len(bounds) else len(text)
        per[name] = [
            source
            for source in re.findall(r'\.copy\(\s*"([^"]+)"', text[start:end])
            if source.startswith(PREFIX)
        ]
    return per


def _shipped_modules(copies: list[str]) -> set[str]:
    """Dotted `sandbox_runtime` names a template puts in the image.

    A copied directory ships everything under it, so it contributes the package
    and each module inside; a copied file contributes just itself.
    """
    shipped: set[str] = set()
    for relative in copies:
        path = BACKEND.parent / relative
        dotted = relative[len("lemma-backend/") :].removesuffix(".py")
        shipped.add(dotted.replace("/", ".").removesuffix(".__init__"))
        if path.is_dir():
            for child in path.rglob("*.py"):
                inside = child.relative_to(BACKEND.parent).as_posix()
                name = inside[len("lemma-backend/") :].removesuffix(".py")
                shipped.add(name.replace("/", ".").removesuffix(".__init__"))
    return shipped


def _imports_of(module: str) -> set[str]:
    """Which `sandbox_runtime.*` names one shipped module imports."""
    path = BACKEND / (module.replace(".", "/") + ".py")
    if not path.is_file():
        path = BACKEND / module.replace(".", "/") / "__init__.py"
    if not path.is_file():
        return set()
    needed: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            "sandbox_runtime"
        ):
            needed.add(node.module or "")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("sandbox_runtime"):
                    needed.add(alias.name)
    return needed


def test_e2b_templates_ship_what_they_import() -> None:
    missing: dict[str, set[str]] = {}
    for template, copies in _copies_per_template().items():
        if not copies:
            continue
        shipped = _shipped_modules(copies)
        for module in sorted(shipped):
            for needed in _imports_of(module):
                # A package is satisfied by the package or by the module file.
                if needed in shipped:
                    continue
                missing.setdefault(f"{template} -> {module}", set()).add(needed)
    assert not missing, (
        "E2B templates ship modules whose imports they do not: "
        + "; ".join(
            f"{where} needs {sorted(names)}" for where, names in missing.items()
        )
    )
