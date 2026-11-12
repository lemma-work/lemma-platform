from enum import Enum


class MachineFileEntryKind(str, Enum):
    DIRECTORY = "directory"
    FILE = "file"
    SYMLINK = "symlink"

    def __str__(self) -> str:
        return str(self.value)
