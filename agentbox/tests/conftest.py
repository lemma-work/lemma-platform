"""Hermetic defaults required before AgentBox modules import global settings."""

from __future__ import annotations

import base64
import os


os.environ.setdefault("AGENTBOX_API_KEY", "agentbox-unit-test-key")
os.environ.setdefault("AGENTBOX_API_URL", "http://agentbox.test")
os.environ.setdefault("AGENTBOX_PROVIDER", "docker")
os.environ.setdefault(
    "AGENTBOX_ENDPOINT_STATE_KEYS",
    base64.urlsafe_b64encode(b"agentbox-endpoint-state-test-key").decode(),
)
