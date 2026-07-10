"""ORM read targets for the cross-module agent context projection."""

from app.modules.identity.infrastructure.models.user_models import User
from app.modules.pod.infrastructure.models.pod_models import Pod

__all__ = ["Pod", "User"]
