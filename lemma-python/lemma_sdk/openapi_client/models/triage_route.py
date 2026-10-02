from enum import Enum


class TriageRoute(str, Enum):
    ACT = "act"
    ASK = "ask"
    DIGEST = "digest"
    IGNORE = "ignore"

    def __str__(self) -> str:
        return str(self.value)
