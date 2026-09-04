"""Every implementation of the auth port must be callable the way the port says.

This exists because a type checker cannot do it here. ``pyproject.toml``
excludes ``app/**/tests/**`` and ``app/modules/test_support/**`` from
basedpyright, so a test double whose signature has drifted from the interface
produces no error anywhere -- and the last time one drifted, adding
``code_verifier`` to ``get_authorization_url``, the unit suite stayed green
while four doubles were broken. Only e2e caught it, and only because those
tests happened to exercise the call.

So the check is made at run time instead, in the fast suite, against the real
providers and the shared double alike. A double is included deliberately: it is
the one most likely to drift, being the one nothing else forces to compile.
"""

from __future__ import annotations

import inspect

import pytest

from app.modules.connectors.services.auth.auth_provider import AuthProviderInterface
from app.modules.connectors.services.auth.composio_auth_provider import (
    ComposioAuthProvider,
)
from app.modules.connectors.services.auth.lemma_auth_provider import LemmaAuthProvider
from app.modules.connectors.tests.support.fake_auth_provider import FakeAuthProvider

IMPLEMENTATIONS = [LemmaAuthProvider, ComposioAuthProvider, FakeAuthProvider]


def _port_methods() -> list[str]:
    return [
        name
        for name, member in inspect.getmembers(
            AuthProviderInterface, inspect.isfunction
        )
        if getattr(member, "__isabstractmethod__", False)
    ]


def test_the_port_still_has_methods_to_check():
    """Guards the guard: a renamed base class or a dropped @abstractmethod would
    otherwise make every assertion below vacuously true."""
    assert set(_port_methods()) == {
        "connect_with_credentials",
        "get_authorization_url",
        "exchange_code_for_credentials",
        "refresh_credentials",
        "revoke_connection",
    }


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS, ids=lambda c: c.__name__)
def test_implementation_accepts_every_parameter_the_port_declares(implementation):
    """A caller writing to the interface must not hit an unexpected-keyword
    TypeError. That is exactly the failure the doubles produced: a 502 with
    `error_type: TypeError` from a keyword the port had grown."""
    for name in _port_methods():
        expected = inspect.signature(getattr(AuthProviderInterface, name))
        actual = inspect.signature(getattr(implementation, name))
        takes_kwargs = any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in actual.parameters.values()
        )
        if takes_kwargs:
            continue
        missing = [
            p for p in expected.parameters if p != "self" and p not in actual.parameters
        ]
        assert not missing, (
            f"{implementation.__name__}.{name} cannot accept {missing}, which "
            f"{AuthProviderInterface.__name__}.{name} declares. A caller writing "
            f"to the port would raise TypeError."
        )


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS, ids=lambda c: c.__name__)
def test_implementation_adds_no_parameter_the_port_cannot_supply(implementation):
    """The other direction. An extra REQUIRED parameter is just as broken: the
    port's callers never pass it, so every call fails."""
    for name in _port_methods():
        expected = inspect.signature(getattr(AuthProviderInterface, name))
        actual = inspect.signature(getattr(implementation, name))
        required_extra = [
            p.name
            for p in actual.parameters.values()
            if p.name != "self"
            and p.name not in expected.parameters
            and p.default is inspect.Parameter.empty
            and p.kind
            not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
        ]
        assert not required_extra, (
            f"{implementation.__name__}.{name} requires {required_extra}, which no "
            f"caller of the port supplies."
        )
