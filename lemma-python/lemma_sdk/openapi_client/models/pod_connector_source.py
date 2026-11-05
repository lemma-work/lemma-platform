from enum import Enum


class PodConnectorSource(str, Enum):
    AUTO = "AUTO"
    IMPORT = "IMPORT"
    MANUAL = "MANUAL"
    MIGRATED = "MIGRATED"

    def __str__(self) -> str:
        return str(self.value)
