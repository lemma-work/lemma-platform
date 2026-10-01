from enum import Enum


class Rung(str, Enum):
    AGENT = "agent"
    MODEL = "model"
    PERSON = "person"
    RULES = "rules"
    SYSTEM_ONE = "system_one"

    def __str__(self) -> str:
        return str(self.value)
