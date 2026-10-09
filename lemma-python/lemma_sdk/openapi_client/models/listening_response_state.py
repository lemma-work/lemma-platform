from enum import Enum


class ListeningResponseState(str, Enum):
    LAPSED = "lapsed"
    LISTENING = "listening"
    PENDING = "pending"
    RETRYING = "retrying"

    def __str__(self) -> str:
        return str(self.value)
