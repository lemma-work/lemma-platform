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


def test_upgrade_removes_retired_agentbox_and_kreuzberg_containers() -> None:
    runtime = RuntimeStub(
        {
            "lemma-local-agentbox",
            "lemma-local-kreuzberg",
            "lemma-local-backend",
        }
    )

    removed = remove_obsolete_containers(runtime)  # type: ignore[arg-type]

    assert removed == ["agentbox", "kreuzberg"]
    assert runtime.removed == ["lemma-local-agentbox", "lemma-local-kreuzberg"]
    assert "lemma-local-backend" in runtime.existing
