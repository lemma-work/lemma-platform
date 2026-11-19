from enum import Enum


class MachineResponseKind(str, Enum):
    CONNECTED = "connected"
    DEFAULT = "default"
    POD = "pod"

    def __str__(self) -> str:
        return str(self.value)
