from enum import Enum


class TriggerSkipReason(str, Enum):
    BULK_WRITE = "bulk_write"
    CONDITION_NOT_MET = "condition_not_met"
    FIRE_CAP = "fire_cap"
    IMPORT_WRITE = "import_write"
    LAG_EXCEEDED = "lag_exceeded"
    OUTSIDE_ROW = "outside_row"

    def __str__(self) -> str:
        return str(self.value)
