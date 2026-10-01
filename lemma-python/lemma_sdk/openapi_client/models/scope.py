from enum import Enum


class Scope(str, Enum):
    PODREAD = "pod:read"
    PODWRITE = "pod:write"

    def __str__(self) -> str:
        return str(self.value)
