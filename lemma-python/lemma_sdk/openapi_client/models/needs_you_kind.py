from enum import Enum


class NeedsYouKind(str, Enum):
    APPROVAL = "APPROVAL"
    CONTACT = "CONTACT"
    DECISION = "DECISION"
    FORM = "FORM"
    PERMISSION = "PERMISSION"
    QUESTION = "QUESTION"

    def __str__(self) -> str:
        return str(self.value)
