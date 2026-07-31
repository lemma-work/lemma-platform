"""Composition entrypoint for the pod-backed authorization implementation."""

from app.modules.pod.services.authorization_factory import (
    create_authorization_service,
)

__all__ = ["create_authorization_service"]
