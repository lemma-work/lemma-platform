from enum import Enum


class IdentityKind(str, Enum):
    EMAIL = "EMAIL"
    HOST = "HOST"
    PHONE = "PHONE"
    SLACK = "SLACK"
    TELEGRAM = "TELEGRAM"

    def __str__(self) -> str:
        return str(self.value)
