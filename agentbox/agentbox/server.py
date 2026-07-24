import os

from agentbox.observability import setup_logging, validate_release_identity
from agentbox.telemetry import instrument_fastapi_app, setup_telemetry

setup_logging(level=os.getenv("AGENTBOX_LOG_LEVEL", os.getenv("LOG_LEVEL", "INFO")))
validate_release_identity()
setup_telemetry()

from agentbox.api.app import app  # noqa: E402

instrument_fastapi_app(app)

__all__ = ["app"]
