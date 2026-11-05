from enum import Enum


class PodTriggerSettingsBulkWritesDefaultType0(str, Enum):
    BATCH = "batch"
    EACH = "each"
    SKIP = "skip"

    def __str__(self) -> str:
        return str(self.value)
