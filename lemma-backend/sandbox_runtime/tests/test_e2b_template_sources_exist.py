"""Every path the E2B templates copy must exist in the repository.

Deliberately parses the builder as text instead of importing it. The other two
E2B template tests `importorskip("e2b")`, so they are skipped in the unit job
and only run in the conformance workflow -- and they drive the builder through
a recording fake that never touches the filesystem. That combination is how the
templates once came to reference a directory for a whole release after it was
deleted: nothing that ran on every commit ever resolved a copy source.

This test needs no SDK and no network, so it runs everywhere and fails the
moment a source path stops existing.
"""

from __future__ import annotations

import re
from pathlib import Path

BUILDER = (
    Path(__file__).resolve().parents[2]
    / "sandbox-images"
    / "templates"
    / "e2b"
    / "build_templates.py"
)


def _repository_root() -> Path:
    """The root the builder itself resolves, not one assumed here.

    Computed the same way `build_templates.REPOSITORY_ROOT` is, so moving the
    builder without updating its `parents[...]` fails this test rather than
    silently pointing the whole build one directory too low.
    """
    text = BUILDER.read_text(encoding="utf-8")
    match = re.search(
        r"REPOSITORY_ROOT = Path\(__file__\)\.resolve\(\)\.parents\[(\d+)\]", text
    )
    assert match is not None, "builder no longer declares REPOSITORY_ROOT"
    return BUILDER.resolve().parents[int(match.group(1))]


def test_repository_root_is_the_monorepo_root() -> None:
    root = _repository_root()
    assert (root / "lemma-python").is_dir()
    assert (root / "lemma-backend" / "sandbox-images").is_dir()


def test_every_copied_source_exists() -> None:
    root = _repository_root()
    sources = sorted(set(re.findall(r'\.copy\(\s*"([^"]+)"', BUILDER.read_text())))
    assert sources, "expected the builder to copy something"
    missing = [rel for rel in sources if not (root / rel).exists()]
    assert not missing, f"E2B templates copy paths that do not exist: {missing}"


def test_function_runtime_lands_where_its_entrypoint_imports_it() -> None:
    """`lemma-function-runtime` does `sys.path.insert(0, "/app")` and imports
    `sandbox_runtime.function.runner`, so the copy destination is a contract
    with that script, not a detail."""
    text = BUILDER.read_text(encoding="utf-8")
    assert '"/app/sandbox_runtime/__init__.py"' in text
    assert '"/app/sandbox_runtime/function"' in text
