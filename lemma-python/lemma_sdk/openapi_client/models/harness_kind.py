from enum import Enum


class HarnessKind(str, Enum):
    HARNESS = "HARNESS"
    LEMMA = "LEMMA"

    def __str__(self) -> str:
        return str(self.value)
