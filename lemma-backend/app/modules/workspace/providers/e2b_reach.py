"""Reaching into an E2B sandbox: a port, and a file placed without a shell.

Its own module because these two are the fabric-specific half of a contract
every provider answers, and they are the half E2B does differently from the
others. Docker reaches a container on a private network and writes a file with
an archive; E2B reaches a public name over the internet and writes through an
SDK that has no notion of file mode. Keeping that difference in one small file
is what stops the ops mixin becoming the place every difference accumulates.
"""

from __future__ import annotations

from datetime import datetime
import shlex

from app.modules.workspace.providers.base import (
    ProviderInstance,
    ProviderRejected,
    SandboxEndpoint,
)
from app.modules.workspace.providers.e2b_common import sdk_errors


class E2BReachMixin:
    """`reach_port` and `deliver_secret` for the E2B fabric."""

    async def reach_port(
        self, instance: ProviderInstance, *, port: int, deadline_at: datetime
    ) -> SandboxEndpoint:
        """The sandbox's own host for a port, and the token that opens it.

        An E2B host is a public name on the internet. When the sandbox was
        created with public traffic disabled it carries a per-sandbox token and
        the edge answers 403 without it; when it was not -- which is every
        sandbox created before that flag existed -- the address is open, and
        `public` says so rather than letting a caller assume otherwise.
        """
        sandbox = await self._connect(instance.provider_id)
        with sdk_errors():
            host = sandbox.get_host(port)
        token = getattr(sandbox, "traffic_access_token", None)
        headers = {"e2b-traffic-access-token": token} if token else {}
        return SandboxEndpoint(
            url=f"https://{host}",
            headers=headers,
            public=not token,
        )

    async def deliver_secret(
        self,
        instance: ProviderInstance,
        *,
        path: str,
        value: bytes,
        deadline_at: datetime,
    ) -> None:
        """Write the file through the SDK, then narrow it to its owner.

        Two steps because the files API has no mode: written and then
        chmod-ed, rather than echoed by a shell, so the bytes never appear in a
        command line where any process in the sandbox could read them out of
        `/proc/<pid>/cmdline`. The value is passed as an argument to
        `files.write`, never interpolated into a command.
        """
        directory, _, name = path.rpartition("/")
        if not name:
            raise ProviderRejected(f"{path!r} does not name a file")
        sandbox = await self._connect(instance.provider_id)
        with sdk_errors(path):
            if directory:
                await sandbox.commands.run(f"mkdir -p {shlex.quote(directory)}")
            await sandbox.files.write(path, value)
            await sandbox.commands.run(f"chmod 600 {shlex.quote(path)}")
