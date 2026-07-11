"""Organization slug normalization at the identity boundary."""

from app.core.helpers.slug import slugify, validate_slug
from app.modules.identity.domain.errors import IdentityValidationError


def normalize_organization_slug(slug: str, name: str) -> str:
    try:
        return validate_slug(slug or slugify(name))
    except ValueError as exc:
        raise IdentityValidationError(str(exc)) from exc
