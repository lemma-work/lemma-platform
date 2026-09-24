"""Build and boot the public frontend without a backend or personal configuration."""

from __future__ import annotations

import os
import selectors
import socket
import subprocess
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlparse
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[3]
FRONTEND = ROOT / "lemma-frontend"


class PublicWebsite:
    def __init__(self, client: httpx.Client) -> None:
        self.client = client

    def reads_public_pages(self) -> None:
        response = self.client.get("/sitemap.xml")
        assert response.status_code == 200
        tree = ET.fromstring(response.text)
        paths = [
            urlparse(node.text or "").path
            for node in tree.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")
        ]
        assert "/docs/getting-started" in paths
        assert "/docs/how-lemma-works" in paths
        assert "/about" in paths
        for path in [*paths, "/privacy", "/tos", "/changelog", "/templates", "/download"]:
            if path.startswith("/import/"):
                continue  # Repository rendering is independently dependent on GitHub availability.
            page = self.client.get(path)
            assert page.status_code == 200, path
            assert "<h1" in page.text, path
        assert self.client.get("/docs/not-a-guide").status_code == 404

    def reads_machine_readable_content(self) -> None:
        for path in [
            "/",
            "/docs",
            "/docs/sdk/client",
            "/about",
            "/contact",
            "/privacy",
            "/tos",
        ]:
            page = self.client.get(path, headers={"Accept": "text/markdown"})
            assert page.status_code == 200
            assert "text/markdown" in page.headers["content-type"]
            assert "accept" in page.headers["vary"].lower()
            assert page.text.startswith("# ")
            html = self.client.get(path, headers={"Accept": "text/html"})
            assert "text/html" in html.headers["content-type"]
        assert "/openapi.json" in self.client.get("/llms.txt").text
        assert self.client.get("/openapi.json").json()["paths"]
        assert self.client.get("/feed.xml").text.startswith("<?xml")

    def follows_existing_links(self) -> None:
        for old, new in [
            ("/login?next=%2Ft", "/auth?next=%2Ft"),
            ("/terms", "/tos"),
            ("/pod/example/data?tab=orders", "/t/example/table/orders"),
            ("/pod/example/conversations/thread", "/t/example/conversation/thread"),
            ("/profile/billing", "/t?settings=plan"),
        ]:
            result = self.client.get(old)
            assert result.status_code in (307, 308)
            assert result.headers["location"] == new
        assert self.client.get("/not-a-real-page").status_code == 404
        assert (
            self.client.get("/api/social-card?title=Example").headers["content-type"]
            == "image/png"
        )
        card = self.client.get("/api/contact-card/pod/example?n=Example&tg=example_bot")
        assert card.status_code == 200
        assert card.text.startswith("BEGIN:VCARD")


@pytest.fixture(scope="module")
def public_site() -> Iterator[PublicWebsite]:
    environment = {
        **os.environ,
        "NEXT_PUBLIC_API_URL": "",
        "NEXT_PUBLIC_AUTH_URL": "",
        "NEXT_PUBLIC_DATA": "live",
        "NEXT_PUBLIC_LEMMA_DEPLOYMENT": "local",
        "NEXT_PUBLIC_ANALYTICS_KEY": "",
        "NEXT_PUBLIC_SITE_URL": "https://example.test",
        "NEXT_TELEMETRY_DISABLED": "1",
    }
    subprocess.run(
        ["npm", "run", "build"],
        cwd=FRONTEND,
        env=environment,
        check=True,
        timeout=300,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    process = subprocess.Popen(
        ["node", "server.mjs", "--port", str(port)],
        cwd=FRONTEND,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        assert process.stdout is not None
        deadline = time.monotonic() + 60
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                assert process.poll() is None, (
                    "Public website exited before becoming ready"
                )
                if selector.select(timeout=max(0, deadline - time.monotonic())):
                    if "Lemma listening on port" in process.stdout.readline():
                        break
            else:
                raise AssertionError("Public website did not become ready")
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30) as client:
            yield PublicWebsite(client)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        if process.stdout:
            process.stdout.close()
