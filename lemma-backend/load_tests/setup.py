"""Load test setup script.

Signs up a dedicated test user, creates an org/pod/table, and writes the
resulting credentials to load_tests/.env.load_test for consumption by k6.

Usage:
    python load_tests/setup.py [--api-url http://localhost:8000]

The app must already be running (e.g. via `make load-test-up`).
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

try:
    import httpx
except ImportError:
    print("httpx not installed — run: uv add httpx", file=sys.stderr)
    sys.exit(1)


def _signup(client: httpx.Client, email: str, password: str) -> str:
    resp = client.post(
        "/st/auth/signup",
        json={
            "formFields": [
                {"id": "email", "value": email},
                {"id": "password", "value": password},
            ]
        },
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "OK":
        raise RuntimeError(f"Signup failed: {data}")
    token = resp.headers.get("st-access-token") or resp.cookies.get("sAccessToken")
    if not token:
        raise RuntimeError("No access token in signup response")
    return token


def _create_org(client: httpx.Client, token: str) -> str:
    resp = client.post(
        "/organizations",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": f"LoadTest Org {uuid.uuid4().hex[:8]}"},
    )
    resp.raise_for_status()
    return resp.json()["id"]


def _create_pod(client: httpx.Client, token: str, org_id: str) -> str:
    suffix = uuid.uuid4().hex[:8]
    resp = client.post(
        "/pods",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": f"LoadTest Pod {suffix}",
            "slug": f"loadtest-pod-{suffix}",
            "type": "ASSISTANT",
            "organization_id": org_id,
        },
    )
    resp.raise_for_status()
    return resp.json()["id"]


def _create_table(
    client: httpx.Client, token: str, pod_id: str, table_name: str
) -> None:
    resp = client.post(
        f"/pods/{pod_id}/datastore/tables",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": table_name,
            "enable_rls": False,
            "columns": [
                {"name": "body", "type": "TEXT", "required": False},
                {"name": "seq", "type": "INTEGER", "required": False},
            ],
        },
    )
    resp.raise_for_status()


def main() -> None:
    parser = argparse.ArgumentParser(description="Provision load test data")
    parser.add_argument(
        "--api-url",
        default="http://localhost:8000",
        help="Base URL of the running lemma-backend (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--email-domain",
        default="lemma-loadtest.com",
        help=(
            "Signup email domain. Needs a normal TLD (reserved ones like .local "
            "fail email-validator's syntax check); it never has to resolve, "
            "because the load-test stack disables the signup email gates."
        ),
    )
    args = parser.parse_args()

    api_url: str = args.api_url.rstrip("/")
    ws_url = api_url.replace("http://", "ws://").replace("https://", "wss://")

    # example.com is on the bundled disposable-email blocklist, and reserved
    # TLDs like .local/.example fail email-validator's syntax check outright.
    # The load-test stack disables both signup email gates, so any normal TLD
    # works and nothing needs to resolve in DNS.
    email = f"loadtest+{uuid.uuid4().hex[:8]}@{args.email_domain}"
    password = "LoadTest@Password123"
    table_name = "load_test_events"

    print(f"Connecting to {api_url} …")
    with httpx.Client(base_url=api_url, timeout=30) as client:
        print(f"  Signing up {email} …")
        token = _signup(client, email, password)

        print("  Creating organisation …")
        org_id = _create_org(client, token)

        print("  Creating pod …")
        pod_id = _create_pod(client, token, org_id)

        print(f"  Creating table '{table_name}' …")
        _create_table(client, token, pod_id, table_name)

    env_path = Path(__file__).parent / ".env.load_test"
    env_path.write_text(
        "\n".join(
            [
                f"LEMMA_API_URL={api_url}",
                f"LEMMA_WS_URL={ws_url}",
                f"LEMMA_TOKEN={token}",
                f"LEMMA_POD_ID={pod_id}",
                f"LEMMA_TABLE_NAME={table_name}",
            ]
        )
        + "\n"
    )

    print(f"\nLoad test env written to {env_path}")
    print(f"  POD_ID   = {pod_id}")
    print(f"  TABLE    = {table_name}")
    print(f"  WS_URL   = {ws_url}/pods/{pod_id}/datastore/changes")


if __name__ == "__main__":
    main()
