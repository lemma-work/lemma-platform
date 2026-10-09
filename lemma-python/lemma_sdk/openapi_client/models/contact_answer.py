from enum import Enum


class ContactAnswer(str, Enum):
    ANYONE = "anyone"
    KNOWN = "known"
    OFF = "off"

    def __str__(self) -> str:
        return str(self.value)
