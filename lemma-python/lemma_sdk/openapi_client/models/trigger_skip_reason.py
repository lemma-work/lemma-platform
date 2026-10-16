from enum import Enum


class TriggerSkipReason(str, Enum):
    BULK_WRITE = "bulk_write"
    FIRE_CAP = "fire_cap"
    IMPORT_WRITE = "import_write"
    LAG_EXCEEDED = "lag_exceeded"

    def __str__(self) -> str:
        return str(self.value)
