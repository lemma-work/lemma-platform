"""The toolbar's browser commands: a closed set, and nothing that runs script.

Back, forward, reload, go to an address, list the tabs, bring one to the
front, close one. That is the whole vocabulary, chosen by what a person
reaches for above a browser they are watching -- and kept small for the reason
`stream_proxy` gives about CDP: every command admitted is one somebody has to
keep safe, and an open-ended one is a way to hand a page `Runtime.evaluate`.

Navigation goes through `agent-browser`, which owns the session's tab
bookkeeping; the agent's next command then finds the page the person moved
to rather than one it thinks is still there. The tab list and the two tab
actions use Chrome's own `/json` endpoints, which act on a target id and take
no script.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Annotated, Literal
from urllib.parse import urlsplit

from collections.abc import Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException
import httpx
from pydantic import BaseModel, ConfigDict, Field

from .chrome import (
    BrowserNotRunning,
    DEFAULT_SESSION,
    agent_browser_argv,
    agent_browser_env,
    live_port,
    page_targets,
)

#: Chrome's target ids are 32 hex characters; anything else is not one.
_TARGET_ID = r"^[A-Fa-f0-9]{8,64}$"

_COMMAND_TIMEOUT_SECONDS = 30.0


class _Command(BaseModel):
    #: An unknown field is refused, not ignored: a command that quietly drops
    #: part of what it was sent is one that can be misread.
    model_config = ConfigDict(extra="forbid")


class Navigate(_Command):
    command: Literal["navigate"]
    url: str = Field(min_length=1, max_length=2048)


class History(_Command):
    command: Literal["back", "forward", "reload"]


class Tabs(_Command):
    command: Literal["tabs"]


class TabAction(_Command):
    command: Literal["activate", "close"]
    target: str = Field(pattern=_TARGET_ID)


BrowserCommand = Annotated[
    Navigate | History | Tabs | TabAction, Field(discriminator="command")
]


class CommandRequest(_Command):
    action: BrowserCommand


class Tab(BaseModel):
    id: str
    title: str
    url: str


class CommandResponse(BaseModel):
    tabs: list[Tab] = Field(default_factory=list)


def address_for(typed: str) -> str:
    """What a person typed in the address bar, as a URL a browser should open.

    Only http and https. Anything with another scheme -- `javascript:`,
    `file:`, `chrome:`, `data:` -- is refused rather than opened: each of them
    is a way to run something or read something the address bar was never
    meant to reach. A bare host gets `https://`.
    """
    text = typed.strip()
    if "://" not in text:
        if ":" in text.split("/", 1)[0] and not text.split(":", 1)[1][:1].isdigit():
            raise ValueError("only web addresses can be opened")
        # A dev server is plain http; anything else is assumed to be https.
        host = text.split("/", 1)[0].split(":", 1)[0].lower()
        local = host in ("localhost", "127.0.0.1") or host.endswith(".localhost")
        text = ("http://" if local else "https://") + text
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("only web addresses can be opened")
    return text


async def _run_cli(*args: str) -> None:
    process = await asyncio.create_subprocess_exec(
        *agent_browser_argv(*args, session=DEFAULT_SESSION),
        env=agent_browser_env(DEFAULT_SESSION),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.wait_for(process.wait(), timeout=_COMMAND_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as exc:
        with suppress(ProcessLookupError):
            process.kill()
        with suppress(ProcessLookupError):
            await process.wait()
        raise HTTPException(
            status_code=504, detail="the browser did not answer"
        ) from exc
    if process.returncode != 0:
        raise HTTPException(status_code=409, detail=f"the browser could not {args[0]}")


async def _tab_action(port: int, action: str, target: str) -> None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"http://127.0.0.1:{port}/json/{action}/{target}")
    if response.status_code != 200:
        raise HTTPException(status_code=404, detail="that tab is not open")


async def run(action: Navigate | History | Tabs | TabAction) -> CommandResponse:
    """Carry out one command, and answer with the tabs as they are after it."""
    try:
        port = await live_port(DEFAULT_SESSION)
    except BrowserNotRunning as exc:
        raise HTTPException(
            status_code=409, detail="the browser is not running"
        ) from exc
    if isinstance(action, Navigate):
        try:
            url = address_for(action.url)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await _run_cli("open", url)
    elif isinstance(action, History):
        await _run_cli(action.command)
    elif isinstance(action, TabAction):
        await _tab_action(port, action.command, action.target)
    try:
        found = await page_targets(port=port)
    except BrowserNotRunning:
        found = []
    return CommandResponse(tabs=[Tab(**target) for target in found])


def register(app: FastAPI, require_token: Callable[..., Awaitable[None]]) -> None:
    """Add these routes to the relay, behind its token check."""
    guarded = [Depends(require_token)]

    @app.post("/browser:command", response_model=CommandResponse, dependencies=guarded)
    async def browser_command(request: CommandRequest) -> CommandResponse:
        return await run(request.action)


__all__ = ["BrowserCommand", "CommandRequest", "address_for", "register", "run"]
