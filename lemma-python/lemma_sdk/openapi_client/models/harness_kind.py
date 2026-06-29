from enum import Enum


class HarnessKind(str, Enum):
    ANTIGRAVITY = "ANTIGRAVITY"
    CLAUDE_CODE = "CLAUDE_CODE"
    CODEX = "CODEX"
    CURSOR = "CURSOR"
    LEMMA = "LEMMA"
    OPENCODE = "OPENCODE"

    def __str__(self) -> str:
        return str(self.value)
