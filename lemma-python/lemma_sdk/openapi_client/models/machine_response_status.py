from enum import Enum


class MachineResponseStatus(str, Enum):
    DELETED = "deleted"
    ERROR = "error"
    OFFLINE = "offline"
    RUNNING = "running"
    STARTING = "starting"
    STOPPED = "stopped"

    def __str__(self) -> str:
        return str(self.value)
