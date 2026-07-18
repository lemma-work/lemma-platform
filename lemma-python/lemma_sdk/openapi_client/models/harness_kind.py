from enum import Enum


class HarnessKind(str, Enum):
    ANTIGRAVITY = "ANTIGRAVITY"
    CLAUDE_CODE = "CLAUDE_CODE"
    CODEX = "CODEX"
    CURSOR = "CURSOR"
    GG_CODER = "GG_CODER"
    LEMMA = "LEMMA"
    OPENCODE = "OPENCODE"

    def __str__(self) -> str:
        return str(self.value)
