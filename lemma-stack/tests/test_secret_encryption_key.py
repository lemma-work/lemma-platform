"""The key that encrypts a self-host's stored credentials is its own.

The backend falls back to a deterministic key when nothing is configured --
`base64(sha256(b"lemma-local-connector-secret-key"))`, a literal in a public
repository. The stack rendered `ENVIRONMENT=local` and never set
`SECRET_ENCRYPTION_KEY`, so every connector credential, auth-config payload and
runtime-profile credential on a `lemma-stack` install was encrypted at rest with
a key any reader of the source can compute.

The installation secret already exists and already derives the workspace runtime
credential; this hangs the encryption key off it the same way, so the key is
per-machine and lives and dies with the data directory.
"""

from __future__ import annotations

import base64
import hashlib

from lemma_stack.config import render, store


#: What the backend derives when nothing is configured
#: (`app/core/crypto/keys.py::local_fallback_secret`). Spelled out here so this
#: test fails if the stack ever renders it again, whatever it is called.
_PUBLISHED_FALLBACK = base64.urlsafe_b64encode(
    hashlib.sha256(b"lemma-local-connector-secret-key").digest()
).decode("ascii")


def _backend_env(paths, **overrides):
    doc = store.load_or_create(paths)
    return doc, render.backend_env(
        doc,
        paths,
        provider="docker",
        workspace_image="workspace:test",
        function_image="function:test",
        container_socket="/var/run/docker.sock",
        **overrides,
    )


def test_the_rendered_encryption_key_is_a_valid_fernet_key(paths):
    _, env = _backend_env(paths)

    key = env["SECRET_ENCRYPTION_KEY"]
    # A Fernet key is 32 raw bytes in urlsafe base64.
    assert len(base64.urlsafe_b64decode(key)) == 32


def test_the_rendered_encryption_key_is_not_the_published_constant(paths):
    _, env = _backend_env(paths)

    assert env["SECRET_ENCRYPTION_KEY"] != _PUBLISHED_FALLBACK


def test_two_installs_do_not_share_a_key(paths, tmp_path):
    from lemma_stack.paths import LocalPaths

    other = LocalPaths(root=tmp_path / "other")
    other.ensure()

    _, first = _backend_env(paths)
    _, second = _backend_env(other)

    assert first["SECRET_ENCRYPTION_KEY"] != second["SECRET_ENCRYPTION_KEY"]


def test_the_key_is_stable_across_renders_so_rows_stay_readable(paths):
    _, first = _backend_env(paths)
    _, second = _backend_env(paths)

    assert first["SECRET_ENCRYPTION_KEY"] == second["SECRET_ENCRYPTION_KEY"]


def test_the_encryption_key_is_not_the_runtime_credential_key(paths):
    """Two derived keys, two domains: one leak must not hand over the other."""
    _, env = _backend_env(paths)

    assert env["SECRET_ENCRYPTION_KEY"] != env["WORKSPACE_RUNTIME_CREDENTIAL_KEY"]


def test_an_operator_override_still_wins(paths):
    doc = store.load_or_create(paths)
    store.set_value(doc, "SECRET_ENCRYPTION_KEY", "operator-supplied")

    env = render.backend_env(
        doc,
        paths,
        provider="docker",
        workspace_image="workspace:test",
        function_image="function:test",
        container_socket="/var/run/docker.sock",
    )

    assert env["SECRET_ENCRYPTION_KEY"] == "operator-supplied"


def test_debug_is_not_rendered_so_local_errors_keep_the_envelope(paths):
    """`DEBUG=true` replaces the error envelope with an HTML traceback."""
    _, env = _backend_env(paths)

    assert "DEBUG" not in env
