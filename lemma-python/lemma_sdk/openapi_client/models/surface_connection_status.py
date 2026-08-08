from enum import Enum


class SurfaceConnectionStatus(str, Enum):
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    MISSING = "MISSING"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"

    def __str__(self) -> str:
        return str(self.value)
