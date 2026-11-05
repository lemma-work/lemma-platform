from enum import Enum


class IdentityKind(str, Enum):
    EMAIL = "EMAIL"
    HOST = "HOST"
    PHONE = "PHONE"
    SLACK = "SLACK"
    TEAMS = "TEAMS"
    TELEGRAM = "TELEGRAM"
    WEB = "WEB"

    def __str__(self) -> str:
        return str(self.value)
