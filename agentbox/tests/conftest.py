"""Hermetic defaults required before AgentBox modules import global settings."""

from __future__ import annotations

import os


os.environ.setdefault("AGENTBOX_API_KEY", "agentbox-unit-test-key")
os.environ.setdefault("AGENTBOX_API_URL", "http://agentbox.test")
os.environ.setdefault("AGENTBOX_PROVIDER", "docker")
