from enum import Enum


class RuntimeProfileProtocol(str, Enum):
    AGENT_HOST = "AGENT_HOST"
    ANTHROPIC_COMPATIBLE = "ANTHROPIC_COMPATIBLE"
    OPENAI_COMPATIBLE = "OPENAI_COMPATIBLE"

    def __str__(self) -> str:
        return str(self.value)
