from enum import Enum


class MeasureStatus(str, Enum):
    COUNTED = "counted"
    FAILED = "failed"
    NOTHING_TO_COUNT = "nothing_to_count"
    NOT_COUNTED = "not_counted"
    TOO_FEW = "too_few"

    def __str__(self) -> str:
        return str(self.value)
