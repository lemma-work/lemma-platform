from enum import Enum


class OutcomePlanAction(str, Enum):
    KEEP = "KEEP"
    SAME = "SAME"
    SET = "SET"

    def __str__(self) -> str:
        return str(self.value)
