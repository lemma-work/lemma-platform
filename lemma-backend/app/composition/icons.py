"""Bind resource modules to the icon storage implementation."""

from app.modules.icon.contracts import IconCleanupPort
from app.modules.icon.services.icon_service import IconService


def create_icon_service() -> IconCleanupPort:
    return IconService()
