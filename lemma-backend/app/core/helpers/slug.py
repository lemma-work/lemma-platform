import re


_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def slugify(text: str) -> str:
    return normalize_public_slug(text)


def is_valid_slug(value: str) -> bool:
    return bool(_SLUG_PATTERN.fullmatch(value))


def validate_slug(value: str) -> str:
    normalized = value.strip()
    if not is_valid_slug(normalized):
        raise ValueError("Slug must contain only lowercase letters, numbers, and hyphens")
    return normalized


def normalize_resource_name(name: str) -> str:
    """Normalize a resource name to lowercase with underscores instead of spaces."""
    return name.strip().lower().replace(" ", "_")


def normalize_public_slug(value: str) -> str:
    """Normalize a public DNS-safe slug."""
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-")
    return normalized
