"""Contract: a column that holds a secret holds a reference into the vault.

Before the vault, each table encrypted its own secret column, and a hand-kept
list of those columns drove key rotation. The list went stale twice -- one
entry named a column a migration had renamed, three encrypted columns were
never added -- and nothing noticed, because nothing compared the list with the
schema. This does, from the other side: every column whose name says it holds
a secret must be a foreign key to ``vault_secrets``, declared with
``vault_owned`` so the secret goes when its owner does; anything else must be
on the list below with the reason it is not a secret.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit

#: Named like a secret, and not one. Each needs its reason.
NOT_SECRETS = {
    ("agent_hosts", "host_secret_hash"): "a hash; the secret is never stored",
    (
        "surface_onboarding_input_tokens",
        "token_hash",
    ): "a hash; the token is never stored",
    ("mcp_oauth_clients", "client_secret_hash"): "a digest; the secret is never stored",
    ("mcp_oauth_tokens", "token_hash"): "a digest; the token is never stored",
    ("agent_surfaces", "credential_mode"): "an enum saying where credentials come from",
    (
        "connect_requests",
        "authorization_url",
    ): "the provider's public authorization URL",
    ("usage_records", "input_tokens"): "a token count",
    ("usage_records", "output_tokens"): "a token count",
    ("usage_records", "cached_input_tokens"): "a token count",
    ("usage_records", "cache_write_tokens"): "a token count",
    (
        "vault_secret_events",
        "secret_id",
    ): "the audit trail's reference, kept after deletion",
}

_PROBE = """
from app.modules.test_support.e2e_base import _import_e2e_models
from app.core.infrastructure.db.base import Base
from app.core.redaction import is_sensitive_key
_import_e2e_models()
for table in Base.metadata.tables.values():
    for column in table.columns:
        if is_sensitive_key(column.name):
            targets = ",".join(f.target_fullname for f in column.foreign_keys)
            owned = column.name in table.info.get("vault_owned_columns", ())
            print(table.name, column.name, targets or "-", owned)
"""


def _sensitive_columns() -> list[tuple[str, str, str, bool]]:
    # A subprocess, so the schema is exactly what a cold process registers --
    # not whatever this test session happened to import first.
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    rows = []
    for line in result.stdout.splitlines():
        table, column, target, owned = line.split()
        rows.append((table, column, target, owned == "True"))
    return rows


def test_every_secret_column_is_a_vault_reference_with_its_release_trigger():
    offenders = []
    for table, column, target, owned in _sensitive_columns():
        if (table, column) in NOT_SECRETS:
            continue
        if target != "vault_secrets.id":
            offenders.append(f"{table}.{column}: not a reference into vault_secrets")
        elif not owned:
            offenders.append(f"{table}.{column}: not declared with vault_owned(...)")
    assert not offenders, (
        "Store secrets in the vault (app.modules.vault.contracts) and keep a "
        "*_secret_id declared with vault_owned; or, if the column is not a "
        "secret, add it to NOT_SECRETS with the reason:\n" + "\n".join(offenders)
    )


def test_the_allowlist_has_no_stale_entries():
    present = {(table, column) for table, column, _, _ in _sensitive_columns()}
    assert set(NOT_SECRETS) <= present, set(NOT_SECRETS) - present
