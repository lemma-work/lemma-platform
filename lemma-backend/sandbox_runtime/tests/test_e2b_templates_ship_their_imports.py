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


def _is_a_module(dotted: str) -> bool:
    """Whether this name is a file or package on disk, rather than something in one.

    `from sandbox_runtime import tasks` and `from sandbox_runtime import x` are
    the same shape to the parser; only the filesystem says which one names a
    module the image has to carry.
    """
    root = BACKEND / dotted.replace(".", "/")
    return root.with_suffix(".py").is_file() or (root / "__init__.py").is_file()


def _source_of(module: str) -> Path | None:
    path = BACKEND / (module.replace(".", "/") + ".py")
    if path.is_file():
        return path
    package = BACKEND / module.replace(".", "/") / "__init__.py"
    return package if package.is_file() else None


def _imports_of(module: str) -> set[str]:
    """Which `sandbox_runtime.*` modules one shipped module needs to exist.

    Three forms, and for a while only the first was seen:

    * ``from sandbox_runtime.tasks import f`` -- the module is `node.module`.
    * ``from sandbox_runtime import tasks`` -- `node.module` is only the
      *package*, which every template ships anyway as `__init__.py`. The module
      that has to be carried is named in `node.names`, so a template omitting
      `tasks.py` passed. Added when the name resolves to a file or package on
      disk, because that is what separates a module from a function.
    * ``from ..tasks import f`` -- `node.module` is `tasks` and does not start
      with `sandbox_runtime` at all, so it was not even looked at. Resolved
      against the package this module lives in.
    """
    source = _source_of(module)
    if source is None:
        return set()
    package = (
        module.rsplit(".", 1)[0]
        if _source_of(module) != (BACKEND / module.replace(".", "/") / "__init__.py")
        else module
    )

    needed: set[str] = set()
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                climbed = base[: len(base) - (node.level - 1)] or base[:1]
                resolved = ".".join([*climbed, *([node.module] if node.module else [])])
            else:
                resolved = node.module or ""
            if not resolved.startswith("sandbox_runtime"):
                continue
            needed.add(resolved)
            for alias in node.names:
                member = f"{resolved}.{alias.name}"
                if _is_a_module(member):
                    needed.add(member)
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
