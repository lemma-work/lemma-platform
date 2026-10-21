from enum import Enum


class MyMembershipUpdateNotifyViaType0Item(str, Enum):
    EMAIL = "EMAIL"
    RESEND = "RESEND"
    SLACK = "SLACK"
    TEAMS = "TEAMS"
    TELEGRAM = "TELEGRAM"
    WHATSAPP = "WHATSAPP"

    def __str__(self) -> str:
        return str(self.value)
