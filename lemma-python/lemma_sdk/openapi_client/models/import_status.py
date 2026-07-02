from enum import Enum


class ImportStatus(str, Enum):
    APPLYING = "APPLYING"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    FETCHING = "FETCHING"
    PLANNING = "PLANNING"
    QUEUED = "QUEUED"

    def __str__(self) -> str:
        return str(self.value)
