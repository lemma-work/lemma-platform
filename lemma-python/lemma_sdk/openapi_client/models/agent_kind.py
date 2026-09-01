from enum import Enum


class AgentKind(str, Enum):
    POD_DEFAULT = "POD_DEFAULT"
    USER = "USER"

    def __str__(self) -> str:
        return str(self.value)
