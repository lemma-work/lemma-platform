from enum import Enum


class QuestionKind(str, Enum):
    APPROVAL = "APPROVAL"
    ASK = "ASK"
    FORM = "FORM"

    def __str__(self) -> str:
        return str(self.value)
