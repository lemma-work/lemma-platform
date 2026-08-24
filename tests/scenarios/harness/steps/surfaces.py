"""Surfaces the pod is reachable on, and the notifications it sends back."""

from __future__ import annotations

from typing import Any

from harness.run import a_name_for
from harness.drivers.api import items_of

JSON = dict[str, Any]


class SurfaceSteps:
    """Mixed into :class:`harness.world.Person`."""

    # --- surfaces --------------------------------------------------------

    async def platforms_available_to(self, pod: JSON) -> list[JSON]:
        return items_of(await self.api.get(f"/pods/{pod['id']}/available-surfaces"))

    async def surfaces_in(self, pod: JSON) -> list[JSON]:
        return items_of(await self.api.get(f"/pods/{pod['id']}/surfaces"))

    async def connects_a_surface(
        self,
        *,
        in_pod: JSON,
        platform: str,
        named: str | None = None,
        agent: str | None = None,
        account: JSON | None = None,
        config: JSON | None = None,
    ) -> JSON:
        body: JSON = {
            "platform": platform,
            "name": named or a_name_for("surface"),
        }
        if agent:
            body["default_agent_name"] = agent
        if account is not None:
            # Where the bot's credentials come from. Without it the platform has
            # nothing to authenticate as, and creation is refused.
            body["account_id"] = str(account["id"])
        if config is not None:
            body["config"] = config
        return await self.api.post(
            f"/pods/{in_pod['id']}/surfaces",
            what=f"{self.label} connecting a {platform} surface",
            json=body,
        )

    async def is_refused_connecting_a_surface(
        self, *, in_pod: JSON, platform: str, config: JSON | None = None
    ) -> int:
        response = await self.api.call(
            "POST",
            f"/pods/{in_pod['id']}/surfaces",
            json={
                "platform": platform,
                "name": a_name_for("surface"),
                **({"config": config} if config is not None else {}),
            },
        )
        if response.status_code < 400:
            raise AssertionError(
                f"{self.label} was expected to be refused connecting a "
                f"{platform} surface, but it succeeded ({response.status_code})"
            )
        return response.status_code

    async def opens_surface(self, name: str, *, in_pod: JSON) -> JSON:
        return await self.api.get(f"/pods/{in_pod['id']}/surfaces/{name}")

    async def deletes_surface(self, name: str, *, in_pod: JSON) -> None:
        await self.api.delete(
            f"/pods/{in_pod['id']}/surfaces/{name}",
            what=f"{self.label} deleting surface {name!r}",
        )

    async def setup_state_of(self, name: str, *, in_pod: JSON) -> JSON:
        return await self.api.get(f"/pods/{in_pod['id']}/surfaces/{name}/setup")

    async def setup_guide_for(self, platform: str, *, in_pod: JSON) -> JSON:
        return await self.api.get(f"/pods/{in_pod['id']}/surface-setup/{platform}")

    async def my_surfaces(self) -> list[JSON]:
        return items_of(await self.api.get("/surfaces/me"))

    # --- notifications ---------------------------------------------------

    async def notifies(
        self,
        person: Any,
        *,
        in_pod: JSON,
        title: str = "Something needs you",
        body: str = "Please take a look.",
        expects_response: bool = False,
    ) -> JSON:
        return await self.api.post(
            f"/pods/{in_pod['id']}/notifications",
            what=f"{self.label} notifying {person.label}",
            json={
                # A plain string: pod member id, user id, or email.
                "recipient": person.email,
                "title": title,
                "body": body,
                "expects_response": expects_response,
            },
        )

    async def notifications_in(self, pod: JSON, **query: Any) -> list[JSON]:
        return items_of(
            await self.api.get(f"/pods/{pod['id']}/notifications", params=query or None)
        )

    async def unread_count_in(self, pod: JSON) -> int:
        payload = await self.api.get(f"/pods/{pod['id']}/notifications/unread-count")
        for key in ("count", "unread", "unread_count", "total"):
            if key in payload:
                return int(payload[key])
        raise AssertionError(f"no unread count in {payload}")

    async def reads(self, notification: JSON, *, in_pod: JSON) -> JSON:
        return await self.api.post(
            f"/pods/{in_pod['id']}/notifications/{notification['id']}/read",
            what=f"{self.label} reading a notification",
        )

    async def reads_everything_in(self, pod: JSON) -> JSON:
        return await self.api.post(f"/pods/{pod['id']}/notifications/read-all")

    async def acknowledges(self, notification: JSON, *, in_pod: JSON) -> JSON:
        return await self.api.post(
            f"/pods/{in_pod['id']}/notifications/{notification['id']}/acknowledge"
        )

    async def answers(self, notification: JSON, *, saying: str, in_pod: JSON) -> JSON:
        return await self.api.post(
            f"/pods/{in_pod['id']}/notifications/{notification['id']}/respond",
            what=f"{self.label} answering a notification",
            json={"summary": saying},
        )

    async def is_refused_answering(
        self, notification: JSON, *, saying: str, in_pod: JSON
    ) -> int:
        response = await self.api.call(
            "POST",
            f"/pods/{in_pod['id']}/notifications/{notification['id']}/respond",
            json={"summary": saying},
        )
        if response.status_code < 400:
            raise AssertionError(
                f"{self.label} was expected to be refused answering this "
                f"notification, but it was accepted ({response.status_code})"
            )
        return response.status_code

    async def changes_surface(self, name: str, *, in_pod: JSON, **changes: Any) -> JSON:
        return await self.api.patch(
            f"/pods/{in_pod['id']}/surfaces/{name}",
            what=f"{self.label} updating surface {name!r}",
            json=changes,
        )

    async def channels_of(self, name: str, *, in_pod: JSON) -> Any:
        return await self.api.call(
            "GET", f"/pods/{in_pod['id']}/surfaces/{name}/channels"
        )

    async def slack_manifest(self) -> Any:
        return await self.api.call("GET", "/surface-setup/slack/manifest")

    async def makes_default_surface(self, surface: JSON, *, platform: str) -> Any:
        return await self.api.call(
            "PUT",
            "/surfaces/me/default",
            json={"platform": platform, "surface_id": str(surface["id"])},
        )
