from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Generator
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from typing import Any
from urllib import error, parse, request

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "e2e: real Docker-backed AgentBox tests")
    config.addinivalue_line("markers", "agentbox: AgentBox tests")


def available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class HttpResponse:
    status_code: int
    headers: dict[str, str]
    text: str

    def json(self) -> dict[str, Any]:
        payload = json.loads(self.text or "{}")
        assert isinstance(payload, dict)
        return payload


class AgentBoxHttpClient:
    def __init__(self, *, base_url: str, api_key: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def request_json(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 120,
    ) -> HttpResponse:
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        request_headers = {"Accept": "application/json", **(headers or {})}
        if self.api_key and "X-API-Key" not in request_headers:
            request_headers["X-API-Key"] = self.api_key
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        return self.raw_request(
            method,
            f"{self.base_url}{path}",
            data=payload,
            headers=request_headers,
            timeout=timeout,
        )

    def raw_request(
        self,
        method: str,
        url: str,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 120,
    ) -> HttpResponse:
        req = request.Request(url, data=data, headers=headers or {}, method=method)
        opener = request.build_opener(_NoRedirectHandler)
        try:
            with opener.open(req, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
                return HttpResponse(
                    status_code=response.status,
                    headers={key.lower(): value for key, value in response.headers.items()},
                    text=raw,
                )
        except error.HTTPError as exc:
            try:
                raw = exc.read().decode("utf-8", errors="replace")
            finally:
                exc.close()
            return HttpResponse(
                status_code=exc.code,
                headers={key.lower(): value for key, value in exc.headers.items()},
                text=raw,
            )


class _NoRedirectHandler(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


@pytest.fixture(scope="session")
def agentbox_runtime_image(repo_root: Path) -> str:
    configured_image = os.environ.get("AGENTBOX_E2E_IMAGE")
    image = configured_image or "agentbox-runtime:e2e"
    if configured_image:
        inspect = subprocess.run(
            ["docker", "image", "inspect", image],
            check=False,
            capture_output=True,
            text=True,
        )
        if inspect.returncode == 0:
            return image

    build = subprocess.run(
        [
            "docker",
            "build",
            "-f",
            str(repo_root / "agentbox" / "Dockerfile.runtime"),
            "-t",
            image,
            str(repo_root),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if build.returncode != 0:
        pytest.fail(
            f"Failed to build AgentBox runtime image {image}.\n"
            f"STDOUT:\n{build.stdout}\nSTDERR:\n{build.stderr}"
        )
    return image


@dataclass
class AgentBoxServer:
    base_url: str
    api_key: str
    app_domain: str

    @property
    def client(self) -> AgentBoxHttpClient:
        return AgentBoxHttpClient(base_url=self.base_url, api_key=self.api_key)

    @property
    def anonymous_client(self) -> AgentBoxHttpClient:
        return AgentBoxHttpClient(base_url=self.base_url)

    def public_get(
        self,
        public_url: str,
        *,
        cookie: str | None = None,
    ) -> HttpResponse:
        parsed = parse.urlsplit(public_url)
        path = parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        headers = {"Host": parsed.netloc}
        if cookie:
            headers["Cookie"] = cookie
        return self.anonymous_client.raw_request(
            "GET",
            f"{self.base_url}{path}",
            headers=headers,
            timeout=120,
        )

    def cleanup_sandbox(self, sandbox_id: str) -> None:
        self.client.request_json("DELETE", f"/sandboxes/{sandbox_id}", timeout=60)


@pytest.fixture(scope="session")
def agentbox_server(
    repo_root: Path,
    agentbox_runtime_image: str,
) -> Generator[AgentBoxServer, None, None]:
    port = available_port()
    api_key = "agentbox-e2e-key"
    base_url = f"http://127.0.0.1:{port}"
    app_domain = f"127-0-0-1.sslip.io:{port}"

    with TemporaryDirectory(prefix="agentbox-e2e-") as tmpdir:
        tmp_path = Path(tmpdir)
        env = {
            **os.environ,
            "PYTHONPATH": str(repo_root / "agentbox"),
            "AGENTBOX_PROVIDER": "docker",
            "AGENTBOX_API_KEY": api_key,
            "AGENTBOX_API_URL": base_url,
            "AGENTBOX_APP_DOMAIN": app_domain,
            "AGENTBOX_RUNTIME_IMAGE": agentbox_runtime_image,
            "AGENTBOX_STATE_DB_PATH": str(tmp_path / "state.db"),
            "AGENTBOX_STORAGE_ROOT": str(tmp_path / "workspaces"),
            "AGENTBOX_ENDPOINT_HOST": "127.0.0.1",
            "AGENTBOX_E2E_LABEL": "true",
            "AGENTBOX_SESSION_IDLE_TIMEOUT_SECONDS": "300",
            "AGENTBOX_SANDBOX_IDLE_TIMEOUT_SECONDS": "300",
            "AGENTBOX_CLEANUP_INTERVAL_SECONDS": "30",
            "AGENTBOX_SANDBOX_READY_TIMEOUT_SECONDS": "240",
        }
        platform = os.environ.get("AGENTBOX_PLATFORM")
        if platform:
            env["AGENTBOX_PLATFORM"] = platform

        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "agentbox.server:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
                "--no-access-log",
            ],
            cwd=repo_root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        server = AgentBoxServer(base_url=base_url, api_key=api_key, app_domain=app_domain)
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    stdout, stderr = proc.communicate(timeout=2)
                    pytest.fail(
                        "AgentBox e2e manager exited during startup.\n"
                        f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}"
                    )
                try:
                    health = server.anonymous_client.request_json("GET", "/health", timeout=2)
                    if health.status_code == HTTPStatus.OK:
                        break
                except error.URLError:
                    pass
                time.sleep(0.25)
            else:
                pytest.fail("Timed out starting AgentBox e2e manager")
            yield server
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


@pytest.fixture
def sandbox_id(request: pytest.FixtureRequest, agentbox_server: AgentBoxServer) -> Generator[str, None, None]:
    value = f"e2e-{request.node.name.lower().replace('_', '-')[:32]}-{os.urandom(4).hex()}"
    yield value
    agentbox_server.cleanup_sandbox(value)


@dataclass
class FakeLemmaFunction:
    pod_id: str
    function_id: str
    name: str
    code: str
    user_id: str
    organization_id: str
    user_email: str = "agentbox-e2e@example.com"
    token: str = "lemma-function-token"


class _FakeLemmaApiHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        fixture: FakeLemmaFunction = self.server.fixture  # type: ignore[attr-defined]
        auth = self.headers.get("Authorization")
        if auth != f"Bearer {fixture.token}":
            self._send(HTTPStatus.UNAUTHORIZED, {"detail": "bad token"})
            return
        path = parse.urlsplit(self.path).path
        if path == "/auth/verify-token":
            self._send(
                HTTPStatus.OK,
                {
                    "user_id": fixture.user_id,
                    "email": fixture.user_email,
                    "pod_id": fixture.pod_id,
                    "organization_id": fixture.organization_id,
                    "function_id": fixture.function_id,
                    "function_name": fixture.name,
                    "scopes": ["function:execute"],
                },
            )
            return
        expected_path = f"/pods/{fixture.pod_id}/functions/{parse.quote(fixture.name, safe='')}"
        if path == expected_path:
            self._send(
                HTTPStatus.OK,
                {
                    "id": fixture.function_id,
                    "name": fixture.name,
                    "pod_id": fixture.pod_id,
                    "type": "API",
                    "code": fixture.code,
                },
            )
            return
        self._send(HTTPStatus.NOT_FOUND, {"detail": "not found"})

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def fake_lemma_function_server() -> Generator[tuple[str, FakeLemmaFunction], None, None]:
    from uuid import uuid4

    function_name = f"agentbox_e2e_{uuid4().hex[:8]}"
    code = f"""#input_type_name: AgentBoxInput
#output_type_name: AgentBoxOutput
#function_name: {function_name}

from pydantic import BaseModel

class AgentBoxInput(BaseModel):
    text: str

class AgentBoxOutput(BaseModel):
    result: str
    user_id: str
    base_url: str

async def {function_name}(ctx, data: AgentBoxInput) -> AgentBoxOutput:
    print("stdout from function")
    return AgentBoxOutput(
        result=data.text.upper(),
        user_id=str(ctx.user_id),
        base_url=ctx.lemma_base_url,
    )
"""
    fixture = FakeLemmaFunction(
        pod_id=str(uuid4()),
        function_id=str(uuid4()),
        name=function_name,
        code=code,
        user_id=str(uuid4()),
        organization_id=str(uuid4()),
    )
    yield from _serve_fake_function(fixture)


@pytest.fixture
def fake_lemma_counter_function_server() -> (
    Generator[tuple[str, FakeLemmaFunction], None, None]
):
    """Serves a function with an OBSERVABLE side effect: it appends one line to a
    per-marker file on every real invocation and returns the line count. The
    returned count reveals whether a re-POSTed run_id actually re-ran the body --
    the real-sandbox analogue of "did we create a second Outlook draft?"."""
    from uuid import uuid4

    function_name = f"agentbox_counter_{uuid4().hex[:8]}"
    code = f"""#input_type_name: CounterInput
#output_type_name: CounterOutput
#function_name: {function_name}

from pathlib import Path
from pydantic import BaseModel

class CounterInput(BaseModel):
    marker: str

class CounterOutput(BaseModel):
    invocations: int

async def {function_name}(ctx, data: CounterInput) -> CounterOutput:
    log = Path("/tmp") / ("agentbox_invocations_" + data.marker + ".log")
    with log.open("a") as handle:
        handle.write("x\\n")
    with log.open() as handle:
        count = sum(1 for _ in handle)
    print(f"invocation {{count}} for marker {{data.marker}}")
    return CounterOutput(invocations=count)
"""
    fixture = FakeLemmaFunction(
        pod_id=str(uuid4()),
        function_id=str(uuid4()),
        name=function_name,
        code=code,
        user_id=str(uuid4()),
        organization_id=str(uuid4()),
    )
    yield from _serve_fake_function(fixture)


def _serve_fake_function(
    fixture: FakeLemmaFunction,
) -> Generator[tuple[str, FakeLemmaFunction], None, None]:
    port = available_port()
    server = ThreadingHTTPServer(("0.0.0.0", port), _FakeLemmaApiHandler)
    server.fixture = fixture  # type: ignore[attr-defined]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        docker_base_url = os.environ.get(
            "AGENTBOX_E2E_DOCKER_LEMMA_API_URL",
            f"http://host.docker.internal:{port}",
        )
        yield docker_base_url, fixture
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture
def fake_lemma_package_function_server() -> (
    Generator[tuple[str, FakeLemmaFunction], None, None]
):
    """A function that declares a pip dependency (cowsay) and imports it at module
    top level — so it only loads/runs if the executor installed it first."""
    from uuid import uuid4

    function_name = f"agentbox_pkg_e2e_{uuid4().hex[:8]}"
    code = f"""#input_type_name: PkgInput
#output_type_name: PkgOutput
#function_name: {function_name}
#python_packages: cowsay

import cowsay
from pydantic import BaseModel

class PkgInput(BaseModel):
    text: str

class PkgOutput(BaseModel):
    rendered: str

async def {function_name}(ctx, data: PkgInput) -> PkgOutput:
    return PkgOutput(rendered=cowsay.get_output_string("cow", data.text))
"""
    fixture = FakeLemmaFunction(
        pod_id=str(uuid4()),
        function_id=str(uuid4()),
        name=function_name,
        code=code,
        user_id=str(uuid4()),
        organization_id=str(uuid4()),
    )
    yield from _serve_fake_function(fixture)
