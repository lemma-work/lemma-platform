from enum import Enum


class SyncRequestDirection(str, Enum):
    FROM_POD = "from_pod"
    TO_POD = "to_pod"

    def __str__(self) -> str:
        return str(self.value)
