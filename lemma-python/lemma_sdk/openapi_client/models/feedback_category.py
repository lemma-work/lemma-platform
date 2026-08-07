from enum import Enum


class FeedbackCategory(str, Enum):
    CLI = "cli"
    DOCS = "docs"
    OTHER = "other"
    PLATFORM = "platform"
    SKILL = "skill"

    def __str__(self) -> str:
        return str(self.value)
