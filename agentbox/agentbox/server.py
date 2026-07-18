from agentbox.observability import setup_logging

setup_logging()

from agentbox.api.app import app  # noqa: E402

__all__ = ["app"]
