from enum import Enum


class PublicWebChallengeReadPurpose(str, Enum):
    CODE = "code"
    ROW = "row"
    SESSION = "session"

    def __str__(self) -> str:
        return str(self.value)
