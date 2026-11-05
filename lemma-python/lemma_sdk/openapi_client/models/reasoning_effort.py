from enum import Enum


class ReasoningEffort(str, Enum):
    HIGH = "high"
    LOW = "low"
    MEDIUM = "medium"
    MINIMAL = "minimal"
    NONE = "none"

    def __str__(self) -> str:
        return str(self.value)
