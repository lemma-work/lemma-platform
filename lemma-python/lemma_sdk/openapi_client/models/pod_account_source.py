from enum import Enum


class PodAccountSource(str, Enum):
    AUTO = "AUTO"
    IMPORT = "IMPORT"
    MANUAL = "MANUAL"
    MIGRATED = "MIGRATED"

    def __str__(self) -> str:
        return str(self.value)
