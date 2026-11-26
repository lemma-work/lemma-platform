from enum import Enum


class ConnectionStanding(str, Enum):
    CONNECT = "connect"
    NOT_ALLOWED = "not_allowed"
    READY = "ready"
    RECONNECT = "reconnect"
    USE_EXISTING = "use_existing"
    WAITING = "waiting"

    def __str__(self) -> str:
        return str(self.value)
