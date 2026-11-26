from enum import Enum


class RunsAsKind(str, Enum):
    APP = "app"
    ME = "me"
    NOBODY = "nobody"
    PERSON = "person"

    def __str__(self) -> str:
        return str(self.value)
