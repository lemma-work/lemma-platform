from enum import Enum


class RuntimeModelCapability(str, Enum):
    IMAGE_OUTPUT = "IMAGE_OUTPUT"
    REASONING = "REASONING"
    TEXT = "TEXT"
    TOOLS = "TOOLS"
    VISION = "VISION"

    def __str__(self) -> str:
        return str(self.value)
