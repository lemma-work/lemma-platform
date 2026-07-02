from enum import Enum


class ExportStatus(str, Enum):
    EXPORTING = "EXPORTING"
    FAILED = "FAILED"
    QUEUED = "QUEUED"
    READY = "READY"

    def __str__(self) -> str:
        return str(self.value)
