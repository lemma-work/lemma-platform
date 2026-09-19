"""Which proxy a sandbox uses, and how a decision is withdrawn.

Nothing here starts a sandbox: `browser_proxy_for` is pure, and `pool` is a
parameter so a test supplies one instead of patching the settings singleton.

The property that changed is withdrawal. This was `random.choice` baked into
the sandbox's creation environment, so the server could give a proxy and
never take it back: clearing the pool left every existing sandbox proxied
until it was replaced, and workspace sandboxes are not replaced on drift.
Measured on the image after the change -- a decision of `p1`, then `p2`,
then empty, produced exactly those three states on Chrome's command line,
and a stale `AGENT_BROWSER_PROXY` no longer wins.
"""

from __future__ import annotations

import pathlib
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from app.modules.workspace.config import WorkspaceSettings
from app.modules.workspace.domain.sandbox import SandboxKind
from app.modules.workspace.services.browser_proxy import (
    browser_proxy_for,
    decision_bytes,
)

pytestmark = pytest.mark.unit

POOL = [SecretStr("http://a:8080"), SecretStr("http://b:8080")]


class TestTheSettingStillParses:
    def test_a_comma_separated_pool_is_split_and_kept_secret(self) -> None:
        settings = WorkspaceSettings(
            WORKSPACE_BROWSER_PROXY_URLS="http://a:1,http://b:2"
        )

        assert [u.get_secret_value() for u in settings.browser_proxy_urls] == [
            "http://a:1",
            "http://b:2",
        ]

    def test_no_pool_configured_means_no_proxy(self) -> None:
        assert WorkspaceSettings().browser_proxy_urls == []


class TestWhoGetsOne:
    def test_a_function_sandbox_never_does(self) -> None:
        """A function sandbox never opens a browser, so giving it one spends
        nothing on anyone."""
        assert browser_proxy_for(uuid4(), SandboxKind.FUNCTION, pool=POOL) is None

    def test_an_empty_pool_means_a_direct_connection(self) -> None:
        assert browser_proxy_for(uuid4(), SandboxKind.WORKSPACE, pool=[]) is None

    def test_a_workspace_gets_one_from_the_pool(self) -> None:
        chosen = browser_proxy_for(uuid4(), SandboxKind.WORKSPACE, pool=POOL)

        assert chosen is not None
        assert chosen.get_secret_value() in {"http://a:8080", "http://b:8080"}


class TestItStaysPut:
    """Stickiness matters because this exists for sign-in pages: a session
    cookie bound to an IP logs the person out when the IP hops."""

    def test_the_same_sandbox_always_gets_the_same_one(self) -> None:
        sandbox = uuid4()

        answers = {
            browser_proxy_for(
                sandbox, SandboxKind.WORKSPACE, pool=POOL
            ).get_secret_value()
            for _ in range(20)
        }

        assert len(answers) == 1

    def test_the_order_of_the_pool_does_not_change_the_answer(self) -> None:
        """A configuration re-read that happens to list the pool differently
        must not move anybody's exit IP."""
        sandbox = uuid4()

        forwards = browser_proxy_for(sandbox, SandboxKind.WORKSPACE, pool=POOL)
        backwards = browser_proxy_for(
            sandbox, SandboxKind.WORKSPACE, pool=list(reversed(POOL))
        )

        assert forwards.get_secret_value() == backwards.get_secret_value()

    def test_different_sandboxes_can_land_on_different_entries(self) -> None:
        seen = {
            browser_proxy_for(
                uuid4(), SandboxKind.WORKSPACE, pool=POOL
            ).get_secret_value()
            for _ in range(40)
        }

        assert len(seen) == 2, (
            "a pool of two that only ever hands out one is not a pool"
        )

    def test_removing_an_entry_moves_only_the_sandboxes_that_held_it(self) -> None:
        """The property that distinguishes rendezvous hashing from modulo,
        and the reason to use it: shrinking the pool must not reshuffle
        everybody's exit IP."""
        three = [
            SecretStr("http://a:1"),
            SecretStr("http://b:2"),
            SecretStr("http://c:3"),
        ]
        sandboxes = [uuid4() for _ in range(60)]
        before = {
            s: browser_proxy_for(
                s, SandboxKind.WORKSPACE, pool=three
            ).get_secret_value()
            for s in sandboxes
        }

        two = [u for u in three if u.get_secret_value() != "http://c:3"]
        after = {
            s: browser_proxy_for(s, SandboxKind.WORKSPACE, pool=two).get_secret_value()
            for s in sandboxes
        }

        moved = [s for s in sandboxes if before[s] != after[s]]
        assert all(before[s] == "http://c:3" for s in moved), (
            "only the sandboxes that held the removed entry may move"
        )

    def test_the_choice_does_not_depend_on_the_process(self) -> None:
        """A digest, not `hash()`: Python salts `hash()` per process, so two
        API workers would disagree about a sandbox's proxy and the exit IP
        would depend on which one served the request."""
        fixed = UUID("12ef6f67-b6bf-40c8-878b-c0adf7a56839")

        assert (
            browser_proxy_for(
                fixed, SandboxKind.WORKSPACE, pool=POOL
            ).get_secret_value()
            == browser_proxy_for(
                fixed, SandboxKind.WORKSPACE, pool=POOL
            ).get_secret_value()
        )


class TestTheDecisionOnTheWire:
    def test_a_proxy_is_written_as_its_url(self) -> None:
        assert decision_bytes(SecretStr("http://a:8080")) == b"http://a:8080"

    def test_no_proxy_is_written_as_empty_not_omitted(self) -> None:
        """Empty is a decision. The sandbox has to tell "the server says no
        proxy" from "the server has not said" -- an older sandbox with a
        baked environment variable needs the first, and a file that is
        merely absent is the second."""
        assert decision_bytes(None) == b""


class TestNothingIsBakedAtCreate:
    def test_the_provisioning_path_no_longer_assigns_a_proxy(self) -> None:
        """A positive assertion, so re-introducing the bake has to argue
        with a named test rather than slip in. It could be given and never
        withdrawn, which is the whole bug."""
        from app.modules.workspace.services import sandbox_service

        source = pathlib.Path(sandbox_service.__file__).read_text()
        assert "provisioning_env" not in source
        assert "AGENT_BROWSER_PROXY" not in source
