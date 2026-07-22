import os

from agentbox.observability import setup_logging

setup_logging(level=os.getenv("AGENTBOX_LOG_LEVEL", os.getenv("LOG_LEVEL", "INFO")))

from agentbox.api.app import app  # noqa: E402

__all__ = ["app"]
