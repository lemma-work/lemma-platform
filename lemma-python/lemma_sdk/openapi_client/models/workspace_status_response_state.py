from enum import Enum


class WorkspaceStatusResponseState(str, Enum):
    ASLEEP = "asleep"
    DOWNLOADING = "downloading"
    READY = "ready"
    STARTING = "starting"
    UNAVAILABLE = "unavailable"

    def __str__(self) -> str:
        return str(self.value)
