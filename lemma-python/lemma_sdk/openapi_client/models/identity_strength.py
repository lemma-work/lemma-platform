from enum import Enum


class IdentityStrength(str, Enum):
    CHANNEL = "CHANNEL"
    MEMBER = "MEMBER"

    def __str__(self) -> str:
        return str(self.value)
