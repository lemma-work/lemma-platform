"""Canonical visibility for apps.

Split out of `AppService` because it is pure validation with no service state:
two callers inside the service, and nothing about it needs a repository, a
storage factory, or an authorization context.
"""

from app.core.authorization.context import (
    ResourceVisibility,
    normalize_resource_visibility,
)
from app.modules.apps.domain.entities import AppEntity
from app.modules.apps.domain.errors import AppValidationError


def app_visibility_value(value: str | None) -> ResourceVisibility:
    """Parse ``value``, rejecting anything unrecognized.

    Apps reject rather than defaulting, so a typo in a bundle surfaces at import
    instead of silently publishing narrower than the author intended.
    """
    visibility = normalize_resource_visibility(value)
    if visibility is None:
        raise AppValidationError("Unsupported app visibility")
    return visibility


def normalize_app_visibility(entity: AppEntity) -> None:
    """Rewrite ``entity.visibility`` in place to its canonical string."""
    entity.visibility = app_visibility_value(entity.visibility).value
