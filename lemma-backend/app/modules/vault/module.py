"""Vault module registration.

First in the installed list: every module that stores a secret reads it
through this one, so its keys are loaded before anything else starts. The
lifespans import the runtime when they run, not when the registry is read, so
importing the app does not pull in the crypto and key providers.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.core.registry import LemmaModule


@asynccontextmanager
async def api_vault_lifespan(app: object) -> AsyncIterator[None]:
    from app.modules.vault.services.runtime import running_vault

    async with running_vault():
        yield


@asynccontextmanager
async def worker_vault_lifespan(context: object) -> AsyncIterator[None]:
    from app.modules.vault.services.runtime import running_vault

    async with running_vault():
        yield


module = LemmaModule(
    name="vault",
    api_lifespans=(api_vault_lifespan,),
    worker_lifespans=(worker_vault_lifespan,),
)
