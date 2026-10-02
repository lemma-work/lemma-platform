from enum import Enum


class RungOutcome(str, Enum):
    ABSTAINED = "abstained"
    ANSWERED = "answered"
    FAILED = "failed"
    NOT_CONFIGURED = "not_configured"
    SKIPPED = "skipped"
    UNAVAILABLE = "unavailable"

    def __str__(self) -> str:
        return str(self.value)
