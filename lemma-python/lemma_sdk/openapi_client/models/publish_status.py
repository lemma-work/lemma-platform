from enum import Enum


class PublishStatus(str, Enum):
    COMPLETED = "COMPLETED"
    EXPORTING = "EXPORTING"
    FAILED = "FAILED"
    PUBLISHING = "PUBLISHING"
    QUEUED = "QUEUED"

    def __str__(self) -> str:
        return str(self.value)
