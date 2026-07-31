from enum import Enum


class RuntimeProfileProtocol(str, Enum):
    AGENT_HOST = "AGENT_HOST"
    ANTHROPIC_COMPATIBLE = "ANTHROPIC_COMPATIBLE"
    AZURE_OPENAI = "AZURE_OPENAI"
    GOOGLE_VERTEX = "GOOGLE_VERTEX"
    OPENAI_COMPATIBLE = "OPENAI_COMPATIBLE"

    def __str__(self) -> str:
        return str(self.value)
