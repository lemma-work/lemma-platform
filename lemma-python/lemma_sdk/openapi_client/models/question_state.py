from enum import Enum


class QuestionState(str, Enum):
    ANSWERED = "ANSWERED"
    ANSWERING = "ANSWERING"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    OPEN = "OPEN"

    def __str__(self) -> str:
        return str(self.value)
