"""Getting started is the one journey that has to begin with a stranger.

Everywhere else the suite runs as a standing cast who sign *in*, because that is
what lets it run against a deployment whose registration gates are up. This
journey is about the gates themselves — signing up, making a first
organization, being invited into one — so it cannot start from somebody who is
already here without testing something else.

That has a cost a deployment feels: an organization cannot be deleted, so every
scenario here that makes one leaves it there for good. Which is why the whole
journey asks first, and skips with a reason where the answer is no. On a target
configured for this suite it runs; on one with its gates up it says so.
"""

from __future__ import annotations

import pytest

from harness.credentials import needs
from harness.environment import OPEN_SIGNUP


@pytest.fixture(autouse=True)
def only_where_a_stranger_can_sign_up():
    needs(OPEN_SIGNUP)
