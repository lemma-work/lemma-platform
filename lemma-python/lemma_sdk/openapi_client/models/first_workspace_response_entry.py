from enum import Enum


class FirstWorkspaceResponseEntry(str, Enum):
    DOMAIN_JOIN = "domain_join"
    EXISTING = "existing"
    NEW_ORG = "new_org"
    SAVED = "saved"
    SURFACE_JOIN = "surface_join"

    def __str__(self) -> str:
        return str(self.value)
