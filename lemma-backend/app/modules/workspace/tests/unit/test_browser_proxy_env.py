"""The proxy pool: parsed from the environment, and assigned only to browsers.

Nothing here starts a sandbox -- `provisioning_env` is the pure function that
decides what a workspace container is created with, and that is what is worth
a unit test on its own: the random choice, and the fact that a function
sandbox (no browser, no proxy budget for it to spend) never gets one.
"""

from __future__ import annotations

from pydantic import SecretStr

from app.modules.workspace.config import WorkspaceSettings
from app.modules.workspace.domain.sandbox import SandboxKind
from app.modules.workspace.services.browser_proxy_env import provisioning_env


def test_a_comma_separated_pool_is_split_and_kept_secret() -> None:
    settings = WorkspaceSettings(
        _env_file=None, WORKSPACE_BROWSER_PROXY_URLS=" http://a:8080 ,http://b:8080"
    )
    assert [s.get_secret_value() for s in settings.browser_proxy_urls] == [
        "http://a:8080",
        "http://b:8080",
    ]
    # `SecretStr`, so printing the setting never puts a proxy address in a log.
    assert "http://a:8080" not in repr(settings.browser_proxy_urls[0])


def test_no_pool_configured_means_no_proxy_assigned() -> None:
    settings = WorkspaceSettings(_env_file=None, WORKSPACE_BROWSER_PROXY_URLS="")
    assert settings.browser_proxy_urls == []


def test_a_function_sandbox_never_gets_a_proxy() -> None:
    """No browser in a function sandbox, so no reason to spend one of the pool
    on it -- and every reason not to, since it is one less available for the
    browsers that actually use it."""
    pool = [SecretStr("http://a:8080")]
    assert provisioning_env(SandboxKind.FUNCTION, pool=pool) == {}


def test_a_workspace_sandbox_gets_one_proxy_from_the_pool() -> None:
    pool = [SecretStr("http://a:8080"), SecretStr("http://b:8080")]
    env = provisioning_env(SandboxKind.WORKSPACE, pool=pool)
    assert env["LEMMA_BROWSER_PROXY_URL"] in {"http://a:8080", "http://b:8080"}


def test_an_empty_pool_assigns_nothing() -> None:
    assert provisioning_env(SandboxKind.WORKSPACE, pool=[]) == {}
