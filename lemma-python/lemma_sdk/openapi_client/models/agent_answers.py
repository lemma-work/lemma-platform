from enum import Enum


class AgentAnswers(str, Enum):
    EVERY_LINE = "EVERY_LINE"
    MENTIONED = "MENTIONED"

    def __str__(self) -> str:
        return str(self.value)
