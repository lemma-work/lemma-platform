from enum import Enum


class PublicAudience(str, Enum):
    ANYONE = "anyone"
    CONTACTS = "contacts"

    def __str__(self) -> str:
        return str(self.value)
