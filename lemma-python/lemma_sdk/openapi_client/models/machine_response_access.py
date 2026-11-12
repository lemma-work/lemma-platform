from enum import Enum


class MachineResponseAccess(str, Enum):
    MANAGE = "manage"
    STATUS = "status"
    USE = "use"

    def __str__(self) -> str:
        return str(self.value)
