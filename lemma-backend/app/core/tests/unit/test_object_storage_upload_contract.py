"""Multipart uploads must not use a part size the object store will reject.

The GCS and S3 XML multipart APIs require every part except the last to be at
least 5 MiB. ``obstore.put_async`` already defaults ``chunk_size`` to exactly
that (5_242_880), so the correct call passes nothing at all.

Three call sites used to override it *down* to 1 MiB — the Azure Blob block-size
convention, left behind by the Azure-to-GCS move. Any staged upload larger than
1 MiB then produced a first part of 1 MiB and failed with::

    EntityTooSmall: Part 1 has size of 1048576 bytes, which is smaller than
                    min part size (5242880 bytes)

Under 1 MiB the upload is a single part, and the last part is exempt from the
minimum, so small files succeeded and hid it. It reached production on datastore
file upload before anyone noticed.

This is a static gate rather than a runtime one because the failure only appears
against a real GCS/S3 endpoint. The e2e counterpart runs a >5 MiB upload against
MinIO, which enforces the same minimum; this test is what runs on every merge.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

#: The ``app`` package itself: this file sits at ``app/core/tests/unit/``.
APP_ROOT = Path(__file__).resolve().parents[3]

#: The GCS/S3 XML multipart minimum, and obstore's own default.
MINIMUM_PART_BYTES = 5 * 1024 * 1024


def _literal_int(node: ast.AST) -> int | None:
    """Fold the small arithmetic that part sizes are conventionally written in.

    ``ast.literal_eval`` rejects ``1024 * 1024``, which is exactly how a byte
    size gets written, so constant-fold the few operators that show up rather
    than forcing call sites into a magic number.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if isinstance(node, ast.BinOp):
        left = _literal_int(node.left)
        right = _literal_int(node.right)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.LShift):
            return left << right
    return None


def _upload_calls() -> list[tuple[Path, int, ast.Call]]:
    """Every ``*.put_async(...)`` call in the app, with its location."""
    found: list[tuple[Path, int, ast.Call]] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        if "/tests/" in path.as_posix():
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover - a parse failure is its own test
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "put_async"
            ):
                found.append((path, node.lineno, node))
    return found


def test_the_scan_actually_finds_the_upload_call_sites():
    """A gate that silently matches nothing passes forever.

    If the uploads move or ``put_async`` is wrapped, this fails and someone
    re-points the scan instead of believing a green tick.
    """
    calls = _upload_calls()

    assert len(calls) >= 3, (
        "expected at least the datastore, app-bundle and pod-bundle-staging "
        f"uploads; found {len(calls)}. Has put_async been renamed or wrapped?"
    )


def test_no_upload_uses_a_part_size_below_the_multipart_minimum():
    offenders: list[str] = []

    for path, lineno, call in _upload_calls():
        chunk_size = next(
            (kw.value for kw in call.keywords if kw.arg == "chunk_size"), None
        )
        if chunk_size is None:
            # The default is already correct; passing nothing is the fix.
            continue

        location = f"{path.relative_to(APP_ROOT)}:{lineno}"
        value = _literal_int(chunk_size)
        if value is None:
            offenders.append(
                f"{location}: chunk_size is not a literal, so this gate cannot "
                "check it. Drop the argument and take obstore's 5 MiB default."
            )
        elif value < MINIMUM_PART_BYTES:
            offenders.append(
                f"{location}: chunk_size={value} is below the {MINIMUM_PART_BYTES}-byte "
                "GCS/S3 multipart minimum. Every part but the last must reach it, so "
                "any upload needing a second part fails with EntityTooSmall."
            )

    assert not offenders, (
        "Multipart uploads below the minimum part size:\n" + "\n".join(offenders)
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("1024 * 1024", 1024 * 1024),
        ("5 * 1024 * 1024", 5 * 1024 * 1024),
        ("5_242_880", 5_242_880),
        ("1 << 20", 1 << 20),
        ("some_setting", None),
    ],
)
def test_part_size_folding(source, expected):
    """The folder is the gate's only moving part, so it gets its own test."""
    assert _literal_int(ast.parse(source, mode="eval").body) == expected
