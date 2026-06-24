"""Identity module E2E fixtures."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio

from app.modules.test_support.e2e import fixtures as e2e_fixtures

pytestmark = pytest.mark.e2e

if TYPE_CHECKING:
    from httpx import AsyncClient

test_network = e2e_fixtures.test_network
postgres_container = e2e_fixtures.postgres_container
supertokens_container = e2e_fixtures.supertokens_container
redis_container = e2e_fixtures.redis_container
test_database_url = e2e_fixtures.test_database_url
test_redis_url = e2e_fixtures.test_redis_url
e2e_settings = e2e_fixtures.e2e_settings
worker = e2e_fixtures.worker
db_manager = e2e_fixtures.db_manager
test_app = e2e_fixtures.test_app
db_session = e2e_fixtures.db_session
async_client = e2e_fixtures.async_client
fixed_test_user = e2e_fixtures.fixed_test_user
authenticated_client = e2e_fixtures.authenticated_client
fixed_test_org = e2e_fixtures.fixed_test_org
scenario = e2e_fixtures.scenario


@pytest_asyncio.fixture(scope="function")
async def signup_user(async_client: "AsyncClient"):
    async def _signup(email: str | None = None, password: str = "TestPassword@123"):
        resolved_email = email or f"test+identity-e2e-{uuid4().hex[:10]}@example.com"
        payload = {
            "formFields": [
                {"id": "email", "value": resolved_email},
                {"id": "password", "value": password},
            ]
        }

        response = await async_client.post("/st/auth/signup", json=payload)
        data = response.json()
        assert response.status_code == 200 and data.get("status") == "OK", data

        access_token = response.headers.get("st-access-token") or response.cookies.get(
            "sAccessToken"
        )
        assert access_token

        return {
            "email": resolved_email,
            "password": password,
            "token": access_token,
            "id": data["user"]["id"],
        }

    return _signup


__all__ = [
    "async_client",
    "authenticated_client",
    "db_manager",
    "db_session",
    "e2e_settings",
    "fixed_test_org",
    "fixed_test_user",
    "postgres_container",
    "redis_container",
    "scenario",
    "signup_user",
    "supertokens_container",
    "test_app",
    "test_database_url",
    "test_network",
    "test_redis_url",
    "worker",
]
