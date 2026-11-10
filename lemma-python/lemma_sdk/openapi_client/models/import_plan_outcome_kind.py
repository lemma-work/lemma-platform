from enum import Enum


class ImportPlanOutcomeKind(str, Enum):
    DATE = "date"
    WEEK = "week"

    def __str__(self) -> str:
        return str(self.value)
