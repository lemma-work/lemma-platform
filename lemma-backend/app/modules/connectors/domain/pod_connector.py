"""What a pod uses: its connector declarations and the accounts in it.

The organization owns installations (``auth_configs``) and accounts, connected
once and usable in every pod a person belongs to. Nothing in a pod stood for
them, so authorization on them was bolted on from outside -- an account's
visibility inferred per calling pod from whether a grant happened to exist, the
connector resource a uuid5 of a catalog slug, a shared account a UUID the model
typed into a tool argument.

Two pod rows replace all of that, and both are ordinary pod resources:

- a **pod connector** is the pod's declaration, under a stable pod-local slug,
  of one org installation it uses, and whether each member brings their own
  account (``PER_MEMBER``) or one shared account serves everyone (``SHARED``);
- a **pod account** records that one org account is in use in the pod. Its
  owner is the account's owner, and only they may widen its visibility --
  PERSONAL (only their own calls), POD, or RESTRICTED to some members.
"""

from __future__ import annotations

import enum
import re
from datetime import datetime
from uuid import UUID

from app.core.domain.entity import Entity


class PodConnectorMode(str, enum.Enum):
    #: Each call uses the acting person's own account.
    PER_MEMBER = "PER_MEMBER"
    #: Each call uses the one pod account the connector names, whoever acts.
    SHARED = "SHARED"


class PodConnectorVisibility(str, enum.Enum):
    POD = "POD"
    RESTRICTED = "RESTRICTED"


class PodAccountVisibility(str, enum.Enum):
    #: Only the owner's own calls (and their agents' calls for them) use it.
    PERSONAL = "PERSONAL"
    #: The owner shared it with the whole pod.
    POD = "POD"
    #: The owner shared it with some members or roles, by grant.
    RESTRICTED = "RESTRICTED"


class PodAccountSource(str, enum.Enum):
    #: Picked up on the owner's first per-member call.
    AUTO = "AUTO"
    #: Added by the owner.
    MANUAL = "MANUAL"
    IMPORT = "IMPORT"
    #: Carried over from the account grants that preceded pod accounts.
    MIGRATED = "MIGRATED"


class PodConnectorSource(str, enum.Enum):
    #: Declared on a person's first use of an installation they have an
    #: account for (``ensure_pod_connector_for_installation``).
    AUTO = "AUTO"
    #: Declared by an editor.
    MANUAL = "MANUAL"
    IMPORT = "IMPORT"
    #: Carried over from the grants that preceded pod connectors.
    MIGRATED = "MIGRATED"


class PodConnectorStatus(str, enum.Enum):
    READY = "READY"
    #: SHARED with no account its owner has shared: someone must share one.
    NEEDS_ACCOUNT = "NEEDS_ACCOUNT"
    #: The installation behind it is disabled.
    UNAVAILABLE = "UNAVAILABLE"


#: A slug is what apps, agents and bundles address a pod connector by, so it is
#: restricted to what reads the same in a URL, a YAML key and a tool argument.
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


class PodConnectorEntity(Entity):
    pod_id: UUID
    slug: str
    auth_config_id: UUID
    mode: PodConnectorMode
    shared_pod_account_id: UUID | None = None
    required: bool = False
    purpose: str | None = None
    visibility: PodConnectorVisibility = PodConnectorVisibility.POD
    source: PodConnectorSource = PodConnectorSource.MANUAL
    created_by_user_id: UUID | None = None


class PodAccountEntity(Entity):
    pod_id: UUID
    account_id: UUID
    #: Copied from the account. It never changes for an account, and having it
    #: here is what lets one partial index say "one default per person per
    #: installation in a pod".
    auth_config_id: UUID
    owner_user_id: UUID
    visibility: PodAccountVisibility = PodAccountVisibility.PERSONAL
    is_member_default: bool = False
    source: PodAccountSource = PodAccountSource.MANUAL
    last_used_at: datetime | None = None
    #: The owner allows the credential inside a sandbox (the git bridge). A
    #: token there is readable by every later command, so sharing an account
    #: for operations does not by itself let a shell hold it.
    allow_sandbox_use: bool = False

    @property
    def is_shared(self) -> bool:
        return self.visibility is not PodAccountVisibility.PERSONAL


def connector_status(
    connector: PodConnectorEntity,
    *,
    installation_active: bool,
    shared_account: PodAccountEntity | None,
) -> PodConnectorStatus:
    """Whether the connector can run at all, independent of who asks."""
    if not installation_active:
        return PodConnectorStatus.UNAVAILABLE
    if connector.mode is PodConnectorMode.SHARED and (
        shared_account is None or not shared_account.is_shared
    ):
        return PodConnectorStatus.NEEDS_ACCOUNT
    return PodConnectorStatus.READY


__all__ = [
    "PodAccountEntity",
    "PodAccountSource",
    "PodAccountVisibility",
    "PodConnectorEntity",
    "PodConnectorMode",
    "PodConnectorSource",
    "PodConnectorStatus",
    "PodConnectorVisibility",
    "SLUG_PATTERN",
    "connector_status",
]
