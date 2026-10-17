from enum import Enum


class IdentityStrength(str, Enum):
    CHANNEL = "CHANNEL"
    CODE = "CODE"
    HOST = "HOST"
    MEMBER = "MEMBER"
    SESSION = "SESSION"

    def __str__(self) -> str:
        return str(self.value)
