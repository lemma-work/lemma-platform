"""Contract: every durable table is named in the docs for the code that owns it.

`docs/modules/<name>.md` is where a module's data model is described, and
`docs/modules/README.md` carries the catalog plus the tables `app/core` owns.
Nothing forced the two to agree, so tables accumulated that no document
mentioned: the onboarding and notification tables, the agent and function wait
and revision tables, the sandbox tables, every authorization and event-delivery
table. Two module rows in the catalog went further and claimed "None" while the
module was writing rows.

A missing entry is not cosmetic. The catalog is what a reader consults before
asking where state lives, and a table absent from it reads as a table that does
not exist -- which is how a second store gets built beside one that was already
there.

This test fails when a new `__tablename__` lands without a line of prose saying
what it holds. Writing that line is the fix; there is no allow-list.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# app/modules/test_support/this_file.py -> lemma-backend/
_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_APP_DIR = _BACKEND_ROOT / "app"
_MODULE_DOCS = _BACKEND_ROOT / "docs" / "modules"
_CORE_DOC = _MODULE_DOCS / "README.md"

# Matches a plain assignment and an annotated one alike, so a model that spells
# the attribute with a `str` annotation is not quietly exempt.
_TABLENAME = re.compile(r"""__tablename__\s*(?::[^=]+)?=\s*["']([A-Za-z0-9_]+)["']""")


def _declared_tables() -> dict[str, list[tuple[str, Path]]]:
    """Every declared table, grouped by the document expected to describe it.

    Keyed on the document rather than the module so that `app/core` tables --
    which belong to no module and are cataloged in the README -- are checked by
    the same rule as everything else.
    """
    found: dict[str, list[tuple[str, Path]]] = {}
    for source in sorted(_APP_DIR.rglob("*.py")):
        # A table declared inside a test file exists for that test alone; it is
        # never migrated and never holds anyone's data, so there is nothing for
        # a module document to tell a reader about it.
        if source.name.startswith("test_"):
            continue
        text = source.read_text(encoding="utf-8")
        names = _TABLENAME.findall(text)
        if not names:
            continue
        parts = source.relative_to(_APP_DIR).parts
        doc = f"{parts[1]}.md" if parts[0] == "modules" else _CORE_DOC.name
        for name in names:
            found.setdefault(doc, []).append((name, source))
    return found


def test_every_declared_table_is_described_in_its_module_document() -> None:
    undocumented: list[str] = []
    for doc_name, tables in sorted(_declared_tables().items()):
        doc_path = _MODULE_DOCS / doc_name
        prose = doc_path.read_text(encoding="utf-8") if doc_path.exists() else ""
        for table, source in tables:
            # Backticked, because that is how every module document names a
            # table; a bare word would also match prose that merely reads like
            # the name.
            if f"`{table}`" not in prose:
                relative = source.relative_to(_BACKEND_ROOT)
                undocumented.append(f"{table} ({relative}) -> docs/modules/{doc_name}")

    assert not undocumented, (
        "These tables are declared but described nowhere. Add a row to the "
        "`| Table | Meaning |` list in the document named after each one, "
        "saying what the table holds and why it is a table:\n  "
        + "\n  ".join(undocumented)
    )
