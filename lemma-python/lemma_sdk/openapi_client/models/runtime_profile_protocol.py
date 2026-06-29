from enum import Enum


class RuntimeProfileProtocol(str, Enum):
    ANTHROPIC_COMPATIBLE = "ANTHROPIC_COMPATIBLE"
    ANTIGRAVITY = "ANTIGRAVITY"
    AZURE_OPENAI = "AZURE_OPENAI"
    CLAUDE_CODE = "CLAUDE_CODE"
    CODEX_APP_SERVER = "CODEX_APP_SERVER"
    CURSOR = "CURSOR"
    GOOGLE_VERTEX = "GOOGLE_VERTEX"
    OPENAI_COMPATIBLE = "OPENAI_COMPATIBLE"
    OPENCODE = "OPENCODE"

    def __str__(self) -> str:
        return str(self.value)
