"""Bundles that ship with the backend, imported by name.

A template goes through the same plan and apply as an uploaded or GitHub bundle.
The one difference is where the archive comes from: it is packed here from
``pod_bundle/templates/<name>/`` instead of being fetched or uploaded, and then
staged exactly like the others.
"""

from __future__ import annotations

import re
from pathlib import Path

from lemma_pod_bundle import POD_MANIFEST_FILE, pack_bundle

from app.modules.pod_bundle.domain.errors import BundleTemplateNotFoundError
from app.modules.pod_bundle.domain.state import TEMPLATE_NAME_PATTERN

TEMPLATES_ROOT = Path(__file__).resolve().parent.parent / "templates"


def shipped_template_names(root: Path = TEMPLATES_ROOT) -> list[str]:
    """Every template this server can import, by name."""
    if not root.is_dir():
        return []
    return sorted(
        path.name
        for path in root.iterdir()
        if template_root(path.name, root=root) is not None
    )


def template_root(name: str, *, root: Path = TEMPLATES_ROOT) -> Path | None:
    """The template's directory, or ``None`` when no template has this name.

    The name comes from a request. The pattern already refuses dots and
    separators; the resolved-parent check is the second line, so a symlink
    dropped into ``templates/`` cannot point an import at the rest of the disk.
    """
    if re.fullmatch(TEMPLATE_NAME_PATTERN, name) is None:
        return None
    candidate = root / name
    if candidate.is_symlink() or not candidate.is_dir():
        return None
    if candidate.resolve().parent != root.resolve():
        return None
    if not (candidate / POD_MANIFEST_FILE).is_file():
        return None
    return candidate


def pack_template(name: str) -> bytes:
    """The named template as archive bytes, ready to stage.

    Blocking filesystem work: callers run it through ``run_blocking``.
    """
    root = template_root(name)
    if root is None:
        raise BundleTemplateNotFoundError(name, available=shipped_template_names())
    return pack_bundle(root)
