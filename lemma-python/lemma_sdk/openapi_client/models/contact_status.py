from enum import Enum


class ContactStatus(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    WAITING_ON_CONTACT = "waiting_on_contact"

    def __str__(self) -> str:
        return str(self.value)
