import re


_POD_NAME_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9 _-]*[A-Za-z0-9])?$")
_MAX_POD_NAME_LENGTH = 255
_POD_NAME_ERROR = (
    "Pod name may contain only letters, numbers, spaces, hyphens, and underscores"
)


def normalize_pod_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise ValueError("Pod name cannot be empty")
    if len(normalized) > _MAX_POD_NAME_LENGTH:
        raise ValueError("Pod name must be 255 characters or fewer")
    if not _POD_NAME_PATTERN.fullmatch(normalized):
        raise ValueError(_POD_NAME_ERROR)
    return normalized
