from enum import Enum


class WriteIntent(str, Enum):
    BULK = "bulk"
    IMPORT = "import"
    NORMAL = "normal"

    def __str__(self) -> str:
        return str(self.value)
