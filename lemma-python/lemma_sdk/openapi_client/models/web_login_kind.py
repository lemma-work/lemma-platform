from enum import Enum


class WebLoginKind(str, Enum):
    CREDENTIAL = "CREDENTIAL"
    SESSION = "SESSION"

    def __str__(self) -> str:
        return str(self.value)
