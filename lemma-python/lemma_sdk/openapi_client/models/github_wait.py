from enum import Enum


class GithubWait(str, Enum):
    CHOOSE = "choose"
    GITHUB_ADMIN = "github_admin"
    GITHUB_PERMISSION = "github_permission"
    INSTALL = "install"
    LEMMA_ADMIN = "lemma_admin"

    def __str__(self) -> str:
        return str(self.value)
