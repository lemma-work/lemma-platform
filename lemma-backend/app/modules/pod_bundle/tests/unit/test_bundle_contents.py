"""How many resources a bundle holds, counted the same at export and import.

``bundle.exported`` and ``import.completed`` both report it. The export's was
always zero and the import's counted plan steps -- a table's rows and an agent's
grants as steps of their own -- so the two could not be compared.
"""

from __future__ import annotations

from pathlib import Path

from lemma_pod_bundle import pack_bundle

from app.modules.pod_bundle.infrastructure.bundle_contents import (
    archived_resource_count,
    resource_count,
)


def _write(path: Path, text: str = "{}") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _bundle(root: Path) -> Path:
    _write(root / "pod.json", '{"name": "p", "format_version": 3}')
    _write(root / "tables" / "leads" / "leads.json")
    _write(root / "tables" / "leads" / "data.csv", "id\n1\n")
    _write(root / "tables" / "accounts" / "accounts.json")
    _write(root / "agents" / "assistant" / "assistant.json")
    _write(root / "agents" / "assistant" / "instruction.md", "Help.")
    _write(root / "connectors" / "crm" / "crm.json")
    _write(root / "functions" / "enrich" / "enrich.json")
    _write(root / "functions" / "enrich" / "code.py", "pass")
    _write(root / "files" / "docs" / "a.pdf", "%PDF")
    _write(root / "files" / "docs" / "b.pdf", "%PDF")
    _write(root / "files" / ".files.json")
    # A stray file at the top of a kind is not a resource; only directories are.
    _write(root / "workflows" / "README.md", "notes")
    (root / "schedules").mkdir()
    return root


def test_one_resource_per_directory_of_a_resource_kind(tmp_path: Path) -> None:
    """Two tables, an agent, a connector and a function. The rows, the
    instruction, the code and the files are parts of those, not resources."""
    assert resource_count(_bundle(tmp_path / "b")) == 5


def test_the_export_and_the_import_count_the_same_bundle_the_same(
    tmp_path: Path,
) -> None:
    root = _bundle(tmp_path / "b")
    assert archived_resource_count(pack_bundle(root)) == resource_count(root)


def test_an_empty_bundle_holds_nothing(tmp_path: Path) -> None:
    root = tmp_path / "empty"
    _write(root / "pod.json")
    assert resource_count(root) == 0
    assert archived_resource_count(pack_bundle(root)) == 0
