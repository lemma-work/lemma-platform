"""Deterministic HTTP contracts for datastore document-processor E2E tests."""

from __future__ import annotations

import asyncio
import base64
from collections import Counter
from uuid import uuid4

from aiohttp import web

_PNG_1X1 = base64.b64encode(
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89"
).decode("ascii")


class FakeDocumentProcessorServer:
    """One local server implementing the Kreuzberg and Docling boundaries."""

    def __init__(self) -> None:
        self.base_url = ""
        self.requests: Counter[str] = Counter()
        self._docling_tasks: dict[str, str] = {}
        self._docling_polls: Counter[str] = Counter()
        self._runner: web.AppRunner | None = None
        #: What ``/health`` answers. False is "Kreuzberg is down" as its
        #: health check reports it, while ``/extract`` still accepts requests.
        self.healthy = True
        #: While set, ``hang-*`` documents get no answer until the hang is
        #: released: an extraction running when its worker is killed.
        self.hang = False
        #: Released by ``forget_traffic`` and ``stop``. A hung handler outlives
        #: the worker that asked, and aiohttp's cleanup waits its full 60s
        #: shutdown timeout for every handler still running.
        self._hung: set[asyncio.Event] = set()
        #: While set, every extraction returns a chunk Postgres cannot store.
        self.nul_chunks = False
        #: The last bytes uploaded under each filename (``echo-*`` returns them).
        self.bodies: dict[str, bytes] = {}

    def forget_traffic(self) -> None:
        """Drop what this server has been asked, keeping it running.

        The server is session-scoped so every test in the module shares it, and
        several of them assert "this document was extracted exactly once". Those
        assertions are only about the test that makes them if the counters start
        empty — otherwise two tests that happen to use the same filename see
        each other's requests, and the second one fails with `2 == 1` for a
        product that did nothing wrong.
        """
        self.requests.clear()
        self.bodies.clear()
        self._docling_tasks.clear()
        self._docling_polls.clear()
        self.healthy = True
        self.hang = False
        self.nul_chunks = False
        self._release_hung()

    def _release_hung(self) -> None:
        for released in self._hung:
            released.set()
        self._hung.clear()

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/health", self._kreuzberg_health)
        app.router.add_post("/extract", self._kreuzberg_extract)
        app.router.add_post("/chunk", self._kreuzberg_chunk)
        app.router.add_post("/v1/convert/file/async", self._docling_submit)
        app.router.add_get("/v1/status/poll/{task_id}", self._docling_poll)
        app.router.add_get("/v1/result/{task_id}", self._docling_result)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        sockets = site._server.sockets if site._server else []  # noqa: SLF001
        assert sockets
        self.base_url = f"http://127.0.0.1:{sockets[0].getsockname()[1]}"

    async def stop(self) -> None:
        self._release_hung()
        if self._runner is not None:
            await self._runner.cleanup()

    async def _multipart_filename(self, request: web.Request) -> str:
        reader = await request.multipart()
        filename = "document.bin"
        async for part in reader:
            body = await part.read(decode=False)
            if part.name == "files" and part.filename:
                filename = part.filename
                self.bodies[filename] = bytes(body)
        return filename

    async def _kreuzberg_health(self, request: web.Request) -> web.Response:
        del request
        self.requests["kreuzberg:health"] += 1
        if self.healthy:
            return web.json_response({"status": "healthy"})
        return web.json_response({"status": "unhealthy"}, status=503)

    async def _kreuzberg_extract(self, request: web.Request) -> web.StreamResponse:
        filename = await self._multipart_filename(request)
        self.requests[f"kreuzberg:{filename}"] += 1
        attempt = self.requests[f"kreuzberg:{filename}"]

        if filename.startswith("poison"):
            # What a deterministic extractor panic on one document looks like:
            # a 500 in milliseconds, every time, while everything else works.
            return web.Response(
                status=500,
                text=(
                    "thread 'tokio-rt-worker' panicked: assertion failed: "
                    "Invalid buffer length"
                ),
            )
        if filename.startswith("hang") and self.hang:
            released = asyncio.Event()
            self._hung.add(released)
            await released.wait()
            return web.Response(status=503)

        if filename.startswith("connection-retry") and attempt == 1:
            transport = request.transport
            if transport is not None:
                transport.close()
            return web.Response(status=503)
        if filename.startswith("config-fallback") and attempt == 1:
            return web.json_response(
                {"error": "enhanced config unsupported"}, status=422
            )
        if filename.startswith("malformed"):
            return web.json_response({"unexpected": True})
        if filename.startswith("provider-error"):
            return web.Response(
                status=503,
                text="upstream api_key=CANARY_DATASTORE_PROVIDER_SECRET",
            )
        if filename.startswith("delayed"):
            await asyncio.sleep(0.4)

        needle = f"Deterministic extracted content for {filename}"
        if filename.startswith("echo-"):
            # The words a revision actually carries, so a test can tell two
            # revisions of one file apart in search.
            needle = self.bodies.get(filename, b"").decode(errors="replace")
        if self.nul_chunks:
            # Postgres refuses a NUL in text, so indexing this fails inside the
            # transaction that replaces the file's chunks.
            needle = f"{needle} \u0000"
        content = (
            ""
            if filename.startswith("pages-only")
            else f"<!-- PAGE 1 -->\n\n# Extracted\n\n{needle}\n\n![](figure.png)"
        )
        chunks = (
            []
            if filename.startswith("chunk-fallback")
            else [
                {
                    "text": needle,
                    "metadata": {"first_page": 1, "last_page": 1},
                }
            ]
        )
        return web.json_response(
            [
                {
                    "content": content,
                    "chunks": chunks,
                    "pages": [
                        {
                            "page_number": 1,
                            "content": needle,
                            "is_blank": False,
                        }
                    ],
                    "images": [
                        {
                            "name": "figure.png",
                            "data": _PNG_1X1,
                            "mime_type": "image/png",
                            "page_number": 1,
                        }
                    ],
                    "mime_type": "application/pdf",
                    "detected_languages": ["eng"],
                }
            ]
        )

    async def _kreuzberg_chunk(self, request: web.Request) -> web.Response:
        payload = await request.json()
        self.requests["kreuzberg:chunk"] += 1
        return web.json_response(
            {"chunks": [{"text": payload.get("text", ""), "metadata": {}}]}
        )

    async def _docling_submit(self, request: web.Request) -> web.Response:
        filename = await self._multipart_filename(request)
        self.requests[f"docling:{filename}"] += 1
        if filename.startswith("docling-submit-error"):
            return web.Response(status=503, text="temporary submit failure")
        task_id = uuid4().hex
        self._docling_tasks[task_id] = filename
        return web.json_response({"task_id": task_id, "task_status": "pending"})

    async def _docling_poll(self, request: web.Request) -> web.Response:
        task_id = request.match_info["task_id"]
        self._docling_polls[task_id] += 1
        filename = self._docling_tasks[task_id]
        if filename.startswith("docling-failure"):
            return web.json_response({"task_id": task_id, "task_status": "failure"})
        status = "started" if self._docling_polls[task_id] == 1 else "success"
        return web.json_response({"task_id": task_id, "task_status": status})

    async def _docling_result(self, request: web.Request) -> web.Response:
        task_id = request.match_info["task_id"]
        filename = self._docling_tasks[task_id]
        if filename.startswith("docling-malformed"):
            return web.json_response({"unexpected": True})
        return web.json_response(
            {
                "document": {
                    "md_content": (
                        f"# Docling output for {filename}\n\n"
                        "<!-- DOCLING_PAGE_BREAK -->\n\nSecond page"
                    )
                }
            }
        )
