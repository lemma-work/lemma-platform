"""Settings this module used to have under a different name.

`DatastoreSettings` is `extra="ignore"`, which is the right default -- a
deployment carrying an unrelated variable should not fail to boot -- and it
means a *renamed* setting is indistinguishable from a typo'd one. The
deployment that had tuned it gets the new default and different behaviour, with
nothing said.

So the old names are kept here and looked for once, at import. Named rather
than read: a rename that also changes what the number means cannot be honoured
by carrying the old value across, and the one below changed from a threshold
that only logged into a switch that decides how search runs. Telling the
operator which knob replaced theirs is the whole of what can be done honestly.
"""

from __future__ import annotations

import os

from app.core.log.log import get_logger

logger = get_logger(__name__)

#: ``old environment variable -> the one that replaced it``.
RENAMED_ENV_VARS = {
    "DATASTORE_SEARCH_VISIBILITY_ID_SOFT_LIMIT": (
        "DATASTORE_SEARCH_READABLE_ID_PUSHDOWN_LIMIT"
    ),
}


def warn_about_renamed_env_vars() -> None:
    """Say once, at import, that a set variable no longer does anything."""
    for old_name, new_name in RENAMED_ENV_VARS.items():
        if os.environ.get(old_name) is None:
            continue
        logger.warning(
            "datastore.config.setting_renamed",
            old_name=old_name,
            new_name=new_name,
        )
