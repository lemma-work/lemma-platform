from __future__ import annotations

from lemma_stack.stack.lifecycle import remove_obsolete_containers


class RuntimeStub:
    def __init__(self, existing: set[str]) -> None:
        self.existing = existing
        self.removed: list[str] = []

    def inspect(self, name: str):
        return {"State": {"Running": True}} if name in self.existing else None

    def remove_container(self, name: str) -> None:
        self.removed.append(name)
        self.existing.discard(name)


def test_upgrade_removes_the_retired_document_service_container() -> None:
    runtime = RuntimeStub({"lemma-local-kreuzberg", "lemma-local-backend"})

    removed = remove_obsolete_containers(runtime)  # type: ignore[arg-type]

    assert removed == ["kreuzberg"]
    assert runtime.removed == ["lemma-local-kreuzberg"]
    assert "lemma-local-backend" in runtime.existing
