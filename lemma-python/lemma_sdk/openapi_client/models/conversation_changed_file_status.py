from enum import Enum


class ConversationChangedFileStatus(str, Enum):
    ADDED = "added"
    DELETED = "deleted"
    MODIFIED = "modified"
    RENAMED = "renamed"
    UNTRACKED = "untracked"

    def __str__(self) -> str:
        return str(self.value)
