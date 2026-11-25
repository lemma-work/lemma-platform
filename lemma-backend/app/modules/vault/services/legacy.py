"""Reading a secret where the previous release wrote it: its Fernet column.

Between the expand migration (``0078_vault_cutover``) and the next release's
contract migration, an owner row whose ``*_secret_id`` is empty may still hold
its value in the old column -- written by a process on the previous release
during the rollout. Owners read it from here, and move it into the vault the
next time they write it. The contract migration copies whatever is left and
drops the old columns, and this module goes with them.

Decrypted in process, on the caller's session: a Fernet token opens in
microseconds, and these reads are rare (the migration already copied every row
that existed). A value under a Cloud KMS data key (``kms+fernet``, written only
by installations that ran the old cipher on Cloud KMS) unwraps over the network
here -- accepted for the one release this path exists.

Never used to write: nothing new is encrypted with the Fernet cipher.
"""

from __future__ import annotations

from app.core.crypto.factory import get_secret_cipher

from app.modules.vault.domain.types import JsonObject


async def legacy_json(value: object) -> JsonObject | None:
    """The plaintext of an old JSON column (any of its encrypted forms)."""
    if value is None:
        return None
    return get_secret_cipher().decrypt_json(value)  # type: ignore[arg-type]


async def legacy_text(value: str | None) -> str | None:
    """The plaintext of an old text column (``lsenc1:`` or bare)."""
    if not value:
        return None
    return get_secret_cipher().decrypt_str(value)
