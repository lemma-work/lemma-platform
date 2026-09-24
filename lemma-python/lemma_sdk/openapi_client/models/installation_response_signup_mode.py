from enum import Enum


class InstallationResponseSignupMode(str, Enum):
    CLOSED = "closed"
    INVITE_ONLY = "invite_only"
    OPEN = "open"

    def __str__(self) -> str:
        return str(self.value)
