from enum import Enum


class SenderKind(str, Enum):
    AGENT = "AGENT"
    CONTACT = "CONTACT"
    MEMBER = "MEMBER"

    def __str__(self) -> str:
        return str(self.value)
