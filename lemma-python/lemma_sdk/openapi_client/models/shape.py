from enum import Enum


class Shape(str, Enum):
    COUNT = "count"
    MEDIAN = "median"
    SHARE = "share"
    TOTAL = "total"

    def __str__(self) -> str:
        return str(self.value)
