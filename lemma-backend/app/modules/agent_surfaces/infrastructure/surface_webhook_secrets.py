"""A surface's webhook secret, kept in the vault.

One text secret per surface, pointed at by `agent_surfaces.webhook_secret_id`.
Only Telegram mints one today: it is the `secret_token` Telegram echoes back on
every delivery, so it is compared on each inbound request and sent once, to
`setWebhook`, whenever the webhook is (re)registered.

The scope comes from the surface *row* -- its organization and pod -- never
from the secret, and the purpose names the column. Both are bound into the
ciphertext (and match migration ``0078_vault_cutover``), so a row repointed at
another surface's secret fails to decrypt rather than verifying with it.

Until the next release's contract migration, a row written by a process on the
previous release may hold its secret only in the old Fernet column
``webhook_secret``; it is read from there and moved on the next write.

Deletion is not here: `vault_owned` on the column removes the secret with the
row, by whatever path the row goes.
"""

from __future__ import annotations

from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.vault.contracts import SecretScope, Vault, legacy_text

WEBHOOK_SECRET_PURPOSE = "agent_surfaces.surface.webhook_secret"
_OWNER_TABLE = "agent_surfaces"


def webhook_secret_scope(model: AgentSurface) -> SecretScope:
    return SecretScope(organization_id=model.organization_id, pod_id=model.pod_id)


async def store_webhook_secret(vault: Vault, model: AgentSurface, secret: str) -> None:
    """Put a first secret, or replace the one the row already points at.

    Replaced in place rather than put anew and repointed: the id stays, so the
    row does not change under a concurrent reader. The old column is cleared in
    the same update, so a process still on the previous release fails closed
    instead of verifying with a stale secret.
    """
    scope = webhook_secret_scope(model)
    if model.webhook_secret_id is None:
        ref = await vault.put(
            scope=scope,
            purpose=WEBHOOK_SECRET_PURPOSE,
            value=secret,
            owner_table=_OWNER_TABLE,
        )
        model.webhook_secret_id = ref.id
    else:
        await vault.replace(
            model.webhook_secret_id,
            expect=scope,
            purpose=WEBHOOK_SECRET_PURPOSE,
            value=secret,
            skip_if_equal=True,
        )
    model.webhook_secret = None


async def reveal_webhook_secret(
    vault: Vault, model: AgentSurface, *, session: object = None
) -> str | None:
    """The row's webhook secret, or None when it has none.

    ``session`` is the one the row was read on, handed back while an old
    row's Fernet value is decrypted (that may call Cloud KMS).
    """
    if model.webhook_secret_id is not None:
        revealed = await vault.reveal(
            model.webhook_secret_id,
            expect=webhook_secret_scope(model),
            purpose=WEBHOOK_SECRET_PURPOSE,
        )
        return revealed.text()
    # Written by the previous release during the rollout.
    return await legacy_text(model.webhook_secret)
