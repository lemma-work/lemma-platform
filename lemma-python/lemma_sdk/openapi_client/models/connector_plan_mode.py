from enum import Enum


class ConnectorPlanMode(str, Enum):
    PER_MEMBER = "PER_MEMBER"
    SHARED = "SHARED"

    def __str__(self) -> str:
        return str(self.value)
