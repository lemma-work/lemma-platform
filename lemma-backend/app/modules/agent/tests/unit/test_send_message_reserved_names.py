"""A client names its own sends; the names the pod gives its own are not theirs.

The turn a channel line starts (``channel-line:{line}``) and the news that a
batch of answers arrived (``replies:{batch}``) are sends the pod makes in a
conversation by name. A client that took one of those names first would turn
the pod's send into a replay of its own.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.agent.api.schemas import SendMessageRequest


@pytest.mark.parametrize("name", ["channel-line:abc", "replies:run-1"])
def test_a_name_the_pod_sends_by_is_refused(name: str):
    with pytest.raises(ValidationError, match="reserved"):
        SendMessageRequest(content="hi", client_message_id=name)


def test_any_other_name_is_the_clients(name: str = "9b2f-retry-1"):
    assert (
        SendMessageRequest(content="hi", client_message_id=name).client_message_id
        == name
    )
