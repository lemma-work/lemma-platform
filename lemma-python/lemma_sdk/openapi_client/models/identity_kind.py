from enum import Enum


class IdentityKind(str, Enum):
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    TELEGRAM = "TELEGRAM"

    def __str__(self) -> str:
        return str(self.value)
