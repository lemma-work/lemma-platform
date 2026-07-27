from enum import Enum


class AgentHostAdapterProtocol(str, Enum):
    ACP_V1 = "ACP_V1"
    NATIVE = "NATIVE"

    def __str__(self) -> str:
        return str(self.value)
