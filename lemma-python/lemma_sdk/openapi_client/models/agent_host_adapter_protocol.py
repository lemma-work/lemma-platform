from enum import Enum


class AgentHostAdapterProtocol(str, Enum):
    ACP = "ACP"
    NATIVE = "NATIVE"

    def __str__(self) -> str:
        return str(self.value)
