"""A Lemma named in a link: `/l/{organization handle}/{Lemma's name}/...`.

The web app spells the address of somewhere in a Lemma in names a person can
read, and turns it back into the pod's id from the reader's own organizations.
Someone the link was sent to may have none of those yet -- a new account, or a
member of another organization -- and then only the server can say which pod
the names mean. Two rules decide it, and both live here.

**The word.** A pod's name becomes one word of an address the way the web app's
``slug`` makes it (``lemma-frontend/src/outcomes/named-address.ts``): lower
case, letters and digits, a hyphen wherever anything else ran. Two pods whose
names make the same word are told apart by neither, so such a word names none.

**Who may be told.** Finding a pod by its name says that a pod of that name
exists, which an id does not: ids cannot be guessed and names can. Pods are
otherwise hidden by name from everyone not in them -- even from members of
their own organization (``list_pods_by_organization``) -- unless they are open
to walk into (``list_joinable_pods``). So a name is answered only to somebody
who could already see that pod by name, or walk into it without asking: its
members, the organization's owner, anyone for a pod open to everyone, and for a
pod open to the organization, anyone in it or free to join it. Every other
name is answered as if no pod had it (PS-ACCESS-031).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from uuid import UUID

from app.modules.identity.contracts import OrganizationRole
from app.modules.pod.domain.pod_entities import PodJoinPolicy

_NOT_A_WORD = re.compile(r"[^a-z0-9]+")


def lemma_word(name: str) -> str:
    """A name as one word of an address; empty for a name with no letters or digits."""
    folded = unicodedata.normalize("NFKD", name).lower()
    return _NOT_A_WORD.sub("-", folded).strip("-")


@dataclass(frozen=True)
class NamedPod:
    """A live pod whose name makes the word asked for."""

    id: UUID
    join_policy: PodJoinPolicy


def findable_by_name(
    *,
    join_policy: PodJoinPolicy,
    in_pod: bool,
    org_role: OrganizationRole | None,
    may_join_org: bool,
) -> bool:
    """Whether this person may learn which pod a name means."""
    if in_pod or org_role == OrganizationRole.ORG_OWNER:
        return True
    if join_policy == PodJoinPolicy.PUBLIC:
        return True
    if join_policy == PodJoinPolicy.ORG_MEMBERS:
        return may_join_org
    return False
