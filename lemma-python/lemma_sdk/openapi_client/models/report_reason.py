from enum import Enum


class ReportReason(str, Enum):
    HARMFUL = "HARMFUL"
    OFFENSIVE = "OFFENSIVE"
    OTHER = "OTHER"
    SEXUAL = "SEXUAL"
    SPAM = "SPAM"

    def __str__(self) -> str:
        return str(self.value)
