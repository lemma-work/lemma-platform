"""What each platform actually POSTs to us, in one place.

Every surface test builds its own inbound payload today. There are two
committed captures and four ad-hoc builders in `helpers.py`, and each matrix
file writes its own interaction payloads inline — so the same Slack
`block_actions` shape is hand-written in four files, the WhatsApp
`button_reply` id format in two, and nothing anywhere checks that any of them
still resembles what the platform sends. A double that drifts does not fail:
it certifies the half we wrote.

This is the inbound half of the contract, matching what
`test_platform_contracts_e2e.py` already does for the outbound half. Two rules
make it a contract rather than another set of guesses:

* **Every builder is pinned.** `test_inbound_payload_contract_e2e.py` runs each
  one through the *production* parser and asserts the parsed result. A builder
  that drifts from the platform fails; so does a parser that stops reading a
  shape the platform really sends. The registry is what that test iterates, so
  a builder cannot be added without being pinned.
* **Captures stay captures.** Where a shape came from a real payload — Teams'
  channel mention carries real AAD object ids and a real
  `19:...@thread.tacv2` conversation id — the builder starts from the committed
  JSON and edits it, rather than restating the fields it remembers.
"""

from __future__ import annotations

from app.modules.agent_surfaces.tests.e2e.platform_payloads.registry import (
    INBOUND_BUILDERS,
    INTERACTION_BUILDERS,
    InboundCase,
    InteractionCase,
)

# Importing each module is what registers its builders.
from app.modules.agent_surfaces.tests.e2e.platform_payloads import (  # noqa: E402,F401
    resend,
    slack,
    teams,
    telegram,
    whatsapp,
)

__all__ = [
    "INBOUND_BUILDERS",
    "INTERACTION_BUILDERS",
    "InboundCase",
    "InteractionCase",
    "resend",
    "slack",
    "teams",
    "telegram",
    "whatsapp",
]
