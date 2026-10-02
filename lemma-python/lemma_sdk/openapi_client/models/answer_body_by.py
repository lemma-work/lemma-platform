from enum import Enum


class AnswerBodyBy(str, Enum):
    AGENT = "agent"
    PERSON = "person"

    def __str__(self) -> str:
        return str(self.value)
