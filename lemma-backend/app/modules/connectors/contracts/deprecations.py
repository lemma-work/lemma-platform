"""What callers written before pod connectors are told to call instead.

One place, because the same retirement shows up on several routes -- execute,
and the ``account_id`` fields of schedules, surfaces and pod bundles -- and the
date and the advice should not drift between them.
"""

from __future__ import annotations

from datetime import date

#: When the account-addressed shapes stop being served. Until then they work,
#: through pod connectors, and every response says what replaces them.
ACCOUNT_ADDRESSING_SUNSET = date(2027, 1, 31)

EXECUTE_REPLACEMENT = (
    "call POST /pods/{pod_id}/connectors/{slug}/operations/{operation}/execute "
    "with the pod connector's slug (SDK: pod.connectors.execute(slug, ...)); "
    "the connector decides the account, so drop account_id"
)

ACCOUNT_ID_REPLACEMENT = (
    "name the pod connector (`pod_connector`, its slug) instead of an account; "
    "the connector decides which account runs"
)
