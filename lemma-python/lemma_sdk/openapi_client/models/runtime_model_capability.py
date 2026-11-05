from enum import Enum


class RuntimeModelCapability(str, Enum):
    REASONING = "REASONING"
    TEXT = "TEXT"
    TOOLS = "TOOLS"
    VISION = "VISION"

    def __str__(self) -> str:
        return str(self.value)
