"""The auth-config memo must not outlive the client it describes.

This was cached in a module-level dict keyed on `id(client)`. Addresses are
reused — CPython hands a freshly allocated object the address a just-freed one
had — so a new client read a dead client's connector listing. When the dead
one's listing had *errored*, which is memoized as `None`, the new client was
told "No connectors are installed in this organization" and the command exited
2.

It reproduced on Linux CI and not on macOS, because whether an address is
reused depends on the allocator's state rather than on anything in the code.
The job that caught it is path-gated and normally skipped, so it had been true
on main for a while before anything ran it.

The collision cannot be forced in-process — whether an address is reused is the
allocator's business, and a test that waits for it skips more often than it
runs. What is asserted here instead is the property that makes a collision
impossible: the answer lives on the client, so there is no address to collide
on and nothing left behind when the client is gone.
"""

from __future__ import annotations

from types import SimpleNamespace

from lemma_cli.cli_core.commands import connectors


class _AuthConfigs:
    """Defined once, at module level, so building a client allocates only the
    namespaces — a fresh class object per call would take the address the
    collision test is watching for."""

    def __init__(self, listing):
        self._listing = listing

    def list(self, *, limit):
        return self._listing()


def _client(listing):
    """A client whose auth-config listing does whatever `listing` does."""
    return SimpleNamespace(
        connectors=SimpleNamespace(auth_configs=_AuthConfigs(listing))
    )


def _errors():
    raise RuntimeError("this organization's listing errored")


def _one_install():
    return {"items": [{"name": "work-gmail", "id": "ac-1"}]}


def test_the_memo_travels_with_the_client_and_nothing_outlives_it() -> None:
    """Deterministic, unlike the collision above: an answer held on the client
    cannot be handed to whoever lands on its address next, because it is gone
    with the client."""
    client = _client(_errors)
    connectors._auth_config_items(client)

    assert hasattr(client, connectors._AUTH_CONFIG_MEMO)
    assert not hasattr(_client(_one_install), connectors._AUTH_CONFIG_MEMO)


def test_two_live_clients_keep_separate_listings() -> None:
    """Both alive at once, so this is about the key, not about lifetimes."""
    empty = _client(lambda: {"items": []})
    installed = _client(_one_install)

    assert connectors._auth_config_items(empty) == []
    assert connectors._auth_config_items(installed) == [
        {"name": "work-gmail", "id": "ac-1"}
    ]


def test_one_client_is_still_only_asked_once() -> None:
    """What the memo is for, and the half a weak-keyed map would have dropped:
    every fake client here is a `SimpleNamespace`, which cannot be weakly
    referenced, so that shape would have memoized nothing under test while
    memoizing fine in production."""
    calls = 0

    def counted():
        nonlocal calls
        calls += 1
        return _one_install()

    client = _client(counted)
    connectors._auth_config_items(client)
    connectors._auth_config_items(client)

    assert calls == 1


def test_a_client_that_refuses_attributes_still_gets_the_right_answer() -> None:
    """Memoization is an optimization; being correct is not."""

    class Slotted:
        __slots__ = ("connectors",)

        def __init__(self):
            self.connectors = SimpleNamespace(
                auth_configs=_AuthConfigs(_one_install)
            )

    assert connectors._auth_config_items(Slotted()) == [
        {"name": "work-gmail", "id": "ac-1"}
    ]
