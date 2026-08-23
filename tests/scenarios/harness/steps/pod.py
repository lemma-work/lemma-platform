"""What a person does with pods and the people in them."""

from __future__ import annotations

from typing import Any

from harness.run import a_name_for, must_be_traceable
from harness.drivers.api import items_of

JSON = dict[str, Any]


def _member_id(member: JSON) -> str:
    """The identifier of a pod membership row.

    The member list returns ``pod_member_id`` — the row also carries ``user_id``
    and ``organization_member_id``, so a bare ``id`` would be ambiguous about
    which of the three it meant. ``id`` is accepted as a fallback so a scenario
    holding the create response works too.
    """
    identifier = member.get("pod_member_id") or member.get("id")
    if not identifier:
        raise AssertionError(
            f"this does not look like a pod membership row: {sorted(member)}"
        )
    return str(identifier)


class PodSteps:
    """Mixed into :class:`harness.world.Person`."""

    # --- pods ------------------------------------------------------------

    async def creates_a_pod(
        self,
        *,
        in_organization: JSON | None = None,
        named: str | None = None,
        pod_type: str = "HYBRID",
        standing: bool = False,
    ) -> JSON:
        organization = in_organization or self.organization
        if organization is None:
            raise AssertionError(
                f"{self.label} has no organization to create a pod in; "
                f"call creates_an_organization() first"
            )
        name = named or a_name_for(f"{self.label}_pod")
        if not standing:
            must_be_traceable(name, what="pod")
        pod = await self.api.post(
            "/pods",
            what=f"{self.label} creating pod {name!r}",
            json={
                "organization_id": str(organization["id"]),
                "name": name,
                "type": pod_type,
            },
        )
        self.pod = pod
        return pod

    async def works_in(self, name: str, *, in_organization: JSON | None = None) -> JSON:
        """Open the pod they work in, making it only if it is not there yet.

        The verb nearly every scenario should use, and it is not a test
        optimisation — it is what a person does. Nobody creates a fresh pod for
        every task; they open the one that is already there, with last quarter's
        tables and somebody else's records still in it.

        Reuse-if-exists is also what makes the suite runnable against a real
        deployment. A pod delete is a soft delete that leaves its schema behind
        for good, so a suite that made one per scenario would grow the target a
        few hundred dead schemas every night.
        """
        organization = in_organization or self.organization
        if organization is None:
            raise AssertionError(
                f"{self.label} has no organization, so there is nowhere for "
                f"{name!r} to be. world.person() sets one; world.new_person() "
                f"does not, because a stranger does not have one yet"
            )
        for pod in await self.pods_in(organization):
            if pod.get("name") == name:
                self.pod = pod
                return pod
        return await self.creates_a_pod(
            in_organization=organization, named=name, standing=True
        )

    async def opens_pod(self, pod: JSON) -> JSON:
        return await self.api.get(
            f"/pods/{pod['id']}", what=f"{self.label} opening pod {pod.get('name')!r}"
        )

    async def pods_in(self, organization: JSON) -> list[JSON]:
        return items_of(
            await self.api.get(f"/pods/organization/{organization['id']}")
        )

    async def sees_pod(self, pod: JSON) -> None:
        listed = await self.pods_in({"id": pod["organization_id"]})
        if not any(str(found["id"]) == str(pod["id"]) for found in listed):
            raise AssertionError(
                f"{self.label} cannot see pod {pod.get('name')!r} in their pod list; "
                f"they see {[found.get('name') for found in listed]}"
            )

    async def does_not_see_pod(self, pod: JSON) -> None:
        listed = await self.pods_in({"id": pod["organization_id"]})
        if any(str(found["id"]) == str(pod["id"]) for found in listed):
            raise AssertionError(
                f"{self.label} can see pod {pod.get('name')!r} in their pod list, "
                f"but should not"
            )

    async def is_refused_pod(self, pod: JSON) -> int:
        response = await self.api.call("GET", f"/pods/{pod['id']}")
        if response.status_code < 400:
            raise AssertionError(
                f"{self.label} was expected to be refused pod {pod.get('name')!r}, "
                f"but it opened with {response.status_code}"
            )
        return response.status_code

    async def deletes_pod(self, pod: JSON) -> None:
        await self.api.delete(
            f"/pods/{pod['id']}", what=f"{self.label} deleting pod {pod.get('name')!r}"
        )

    async def opens_pod_to(self, pod: JSON, *, who: str) -> JSON:
        """Change who may walk into a pod: ``INVITE_ONLY``, ``ORG_MEMBERS``, ``PUBLIC``."""
        return await self.api.put(
            f"/pods/{pod['id']}",
            what=f"{self.label} setting the join policy of {pod.get('name')!r}",
            json={"config": {"join_policy": who}},
        )

    async def joins(self, pod: JSON) -> JSON:
        return await self.api.post(
            f"/pods/{pod['id']}/join", what=f"{self.label} joining {pod.get('name')!r}"
        )

    async def is_refused_joining(self, pod: JSON) -> int:
        response = await self.api.call("POST", f"/pods/{pod['id']}/join")
        if response.status_code < 400:
            raise AssertionError(
                f"{self.label} was expected to be refused joining "
                f"{pod.get('name')!r}, but joined ({response.status_code})"
            )
        return response.status_code

    async def requests_to_join(self, pod: JSON) -> JSON:
        return await self.api.post(
            f"/pods/{pod['id']}/join-requests",
            what=f"{self.label} requesting access to {pod.get('name')!r}",
        )

    async def join_requests_for(self, pod: JSON) -> list[JSON]:
        return items_of(await self.api.get(f"/pods/{pod['id']}/join-requests"))

    async def approves(
        self,
        join_request: JSON,
        *,
        for_pod: JSON,
        as_role: str = "POD_USER",
        org_role: str = "ORG_MEMBER",
    ) -> JSON:
        return await self.api.post(
            f"/pods/{for_pod['id']}/join-requests/{join_request['id']}/approve",
            what=f"{self.label} approving a join request for {for_pod.get('name')!r}",
            json={"pod_role": as_role, "org_role": org_role},
        )

    async def is_refused_approving(
        self, join_request: JSON, *, for_pod: JSON, org_role: str = "ORG_OWNER"
    ) -> int:
        response = await self.api.call(
            "POST",
            f"/pods/{for_pod['id']}/join-requests/{join_request['id']}/approve",
            json={"pod_role": "POD_USER", "org_role": org_role},
        )
        if response.status_code < 400:
            raise AssertionError(
                f"{self.label} was expected to be refused approving with org role "
                f"{org_role}, but it succeeded ({response.status_code})"
            )
        return response.status_code

    # --- custom roles ----------------------------------------------------

    async def creates_a_role(
        self, *, in_pod: JSON, named: str, permissions: list[str]
    ) -> JSON:
        return await self.api.post(
            f"/pods/{in_pod['id']}/roles",
            what=f"{self.label} creating role {named!r}",
            json={"name": named, "permission_ids": permissions},
        )

    async def roles_in(self, pod: JSON) -> list[JSON]:
        return items_of(await self.api.get(f"/pods/{pod['id']}/roles"))

    async def permission_catalog_of(self, pod: JSON) -> list[JSON]:
        return items_of(await self.api.get(f"/pods/{pod['id']}/permissions/catalog"))

    async def is_refused_deleting_pod(self, pod: JSON) -> int:
        response = await self.api.call("DELETE", f"/pods/{pod['id']}")
        if response.status_code < 400:
            raise AssertionError(
                f"{self.label} was expected to be refused deleting pod "
                f"{pod.get('name')!r}, but it succeeded ({response.status_code})"
            )
        return response.status_code

    # --- pod membership --------------------------------------------------

    async def adds(
        self, person: Any, *, to_pod: JSON, as_role: str = "POD_VIEWER"
    ) -> JSON:
        """Add someone already in the organization to a pod.

        Pod membership is keyed by *organization* membership, not by user, so
        this resolves the organization member first. That indirection is the
        product rule that an organization is the outer boundary a pod cannot
        widen — worth keeping visible here rather than hiding it in a helper.
        """
        organization_id = to_pod["organization_id"]
        members = items_of(
            await self.api.get(f"/organizations/{organization_id}/members")
        )
        match = next(
            (m for m in members if str(m.get("user_id")) == str(person.user_id)), None
        )
        if match is None:
            raise AssertionError(
                f"{person.label} is not a member of the organization owning pod "
                f"{to_pod.get('name')!r}, so cannot be added to it"
            )
        return await self.api.post(
            f"/pods/{to_pod['id']}/members",
            what=f"{self.label} adding {person.label} to {to_pod.get('name')!r}",
            # `roles` is a list: a member can hold several, including custom ones.
            json={"organization_member_id": str(match["id"]), "roles": [as_role]},
        )

    async def is_refused_adding(
        self, person: Any, *, to_pod: JSON, as_role: str = "POD_VIEWER"
    ) -> int:
        organization_id = to_pod["organization_id"]
        members = items_of(
            await self.api.get(f"/organizations/{organization_id}/members")
        )
        match = next(
            (m for m in members if str(m.get("user_id")) == str(person.user_id)), None
        )
        if match is None:
            raise AssertionError(
                f"{person.label} is not in the organization, so this would be "
                f"refused for the wrong reason"
            )
        response = await self.api.call(
            "POST",
            f"/pods/{to_pod['id']}/members",
            json={"organization_member_id": str(match["id"]), "roles": [as_role]},
        )
        if response.status_code < 400:
            raise AssertionError(
                f"{self.label} was expected to be refused adding {person.label} "
                f"to {to_pod.get('name')!r}, but it succeeded "
                f"({response.status_code})"
            )
        return response.status_code

    async def membership_of(self, person: Any, *, in_pod: JSON) -> JSON:
        for member in await self.members_of_pod(in_pod):
            if str(member.get("user_id")) == str(person.user_id):
                return member
        raise AssertionError(
            f"{person.label} is not a member of pod {in_pod.get('name')!r}"
        )

    async def gives(self, person: Any, *, roles: list[str], in_pod: JSON) -> JSON:
        membership = await self.membership_of(person, in_pod=in_pod)
        return await self.api.patch(
            f"/pods/{in_pod['id']}/members/{_member_id(membership)}/roles",
            what=f"{self.label} re-roling {person.label} in {in_pod.get('name')!r}",
            json={"roles": roles},
        )

    async def is_refused_giving(
        self, person: Any, *, roles: list[str], in_pod: JSON
    ) -> int:
        membership = await self.membership_of(person, in_pod=in_pod)
        response = await self.api.call(
            "PATCH",
            f"/pods/{in_pod['id']}/members/{_member_id(membership)}/roles",
            json={"roles": roles},
        )
        if response.status_code < 400:
            raise AssertionError(
                f"{self.label} was expected to be refused re-roling "
                f"{person.label} to {roles} in {in_pod.get('name')!r}, but it "
                f"succeeded ({response.status_code})"
            )
        return response.status_code

    async def members_of_pod(self, pod: JSON) -> list[JSON]:
        return items_of(await self.api.get(f"/pods/{pod['id']}/members"))

    async def removes_member(self, member: JSON, *, from_pod: JSON) -> None:
        await self.api.delete(
            f"/pods/{from_pod['id']}/members/{_member_id(member)}",
            what=f"{self.label} removing a member from {from_pod.get('name')!r}",
        )

    async def is_refused_removing(self, member: JSON, *, from_pod: JSON) -> int:
        response = await self.api.call(
            "DELETE", f"/pods/{from_pod['id']}/members/{_member_id(member)}"
        )
        if response.status_code < 400:
            raise AssertionError(
                f"{self.label} was expected to be refused removing a member from "
                f"{from_pod.get('name')!r}, but it succeeded "
                f"({response.status_code})"
            )
        return response.status_code

    # --- what a person may do --------------------------------------------

    async def permissions_in(self, pod: JSON) -> set[str]:
        """Exactly what this person may do in this pod, as the API reports it.

        The field is ``actions``. Reading a key that does not exist would return
        an empty set, and every "holds no write permission" assertion would pass
        against nothing — so this fails loudly instead of guessing.
        """
        payload = await self.api.get(f"/pods/{pod['id']}/permissions/me")
        if "actions" not in payload:
            raise AssertionError(
                f"effective permissions for {self.label} carried no 'actions'; "
                f"got keys {sorted(payload)}"
            )
        return set(payload["actions"])

    async def may(self, permission: str, *, in_pod: JSON) -> bool:
        return permission in await self.permissions_in(in_pod)

    async def can_read(self, pod: JSON) -> None:
        await self.opens_pod(pod)

    async def cannot_write_to(self, pod: JSON) -> None:
        held = await self.permissions_in(pod)
        if not held:
            raise AssertionError(
                f"{self.label} holds no permissions at all in "
                f"{pod.get('name')!r}, so this proves nothing about writing"
            )
        writes = {p for p in held if p.endswith((".create", ".update", ".delete"))}
        if writes:
            raise AssertionError(
                f"{self.label} was expected to hold no write permissions in "
                f"{pod.get('name')!r}, but holds {sorted(writes)}"
            )

    async def opens_membership(self, member: JSON, *, in_pod: JSON) -> JSON:
        return await self.api.get(
            f"/pods/{in_pod['id']}/members/{_member_id(member)}"
        )

    async def finds_member_by_email(self, email: str, *, in_pod: JSON) -> JSON:
        return await self.api.get(
            f"/pods/{in_pod['id']}/members/lookup/by-email", params={"email": email}
        )

    async def finds_member_by_user(self, user_id: str, *, in_pod: JSON) -> JSON:
        return await self.api.get(
            f"/pods/{in_pod['id']}/members/lookup/by-user-id/{user_id}"
        )

    async def permissions_of_role(self, name: str, *, in_pod: JSON) -> JSON:
        return await self.api.get(f"/pods/{in_pod['id']}/roles/{name}/permissions")

    async def replaces_role_permissions(
        self, name: str, *, grants: list[JSON], in_pod: JSON
    ) -> JSON:
        """Replace what a role may do on named resources.

        A grant here is resource-scoped —
        ``{"resource_type", "resource_name", "permission_ids"}`` — not a flat
        list of permissions. A role's *general* permissions are set with
        `pod.roles.update`; this is the per-resource layer on top.
        """
        return await self.api.put(
            f"/pods/{in_pod['id']}/roles/{name}/permissions",
            what=f"{self.label} replacing what {name!r} may reach",
            json={"grants": grants},
        )

    async def changes_role_definition(
        self, name: str, *, in_pod: JSON, **changes: Any
    ) -> JSON:
        """Change a custom role — its description, or what it may generally do.

        ``name`` is required by the API even when it is not changing, because it
        identifies the role being described rather than requesting a rename.
        """
        return await self.api.patch(
            f"/pods/{in_pod['id']}/roles/{name}",
            what=f"{self.label} updating role {name!r}",
            json={"name": name, **changes},
        )

    async def deletes_role(self, name: str, *, in_pod: JSON) -> None:
        await self.api.delete(
            f"/pods/{in_pod['id']}/roles/{name}",
            what=f"{self.label} deleting role {name!r}",
        )

    async def my_join_request_for(self, pod: JSON) -> Any:
        response = await self.api.call("GET", f"/pods/{pod['id']}/join-requests/me")
        return response.json() if response.status_code == 200 else None

    async def previews(self, resource_type: str, *, named: str, in_pod: JSON) -> JSON:
        return await self.api.get(
            f"/pods/{in_pod['id']}/resources/{resource_type}/preview",
            params={"name": named},
        )
