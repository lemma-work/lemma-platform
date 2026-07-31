from enum import Enum


class ImportStatus(str, Enum):
    APPLYING = "APPLYING"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    CANCELLED = "CANCELLED"
    CANCELLING = "CANCELLING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    FETCHING = "FETCHING"
    PARTIALLY_CANCELLED = "PARTIALLY_CANCELLED"
    PLANNING = "PLANNING"
    QUEUED = "QUEUED"

    def __str__(self) -> str:
        return str(self.value)
