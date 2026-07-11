"""Public icon service ports."""

from typing import Protocol


class IconCleanupPort(Protocol):
    async def delete_by_url(self, icon_url: str | None) -> None: ...


__all__ = ["IconCleanupPort"]
