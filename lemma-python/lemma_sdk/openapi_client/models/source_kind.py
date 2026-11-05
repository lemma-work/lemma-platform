from enum import Enum


class SourceKind(str, Enum):
    CHAT = "CHAT"
    PERSON = "PERSON"

    def __str__(self) -> str:
        return str(self.value)
