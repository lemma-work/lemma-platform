"""Serve the stream bench's probe page, and answer the runner from inside the sandbox.

Stdlib only, so it runs on whatever Python the sandbox image has and adds
nothing to it: the bench copies this file and `stream-probe.html` in when it
starts and nothing in the image refers to either.

    GET  /         the probe page
    GET  /now      this machine's wall clock, for the runner's NTP-style pings
    GET  /mode     the scenario the page should show; the page polls it
    POST /mode?m=  set it (idle | scroll | animation | typing)
    POST /open     put this page in front, in the browser the live view shows
    POST /report   the page's own paint rate, once a second
    GET  /stats    cumulative CPU per streaming process, read from /proc

Run: `python3 stream-probe-server.py [port]` (default 8765).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

PAGE = Path(__file__).with_name("stream-probe.html")
MODES = ("idle", "scroll", "animation", "typing")

state: dict[str, object] = {"mode": "idle", "report": None, "report_at": None}


def _process_group(comm: str, cmdline: str) -> str | None:
    """Which part of the streaming chain a process belongs to, if any."""
    if comm == "x11vnc":
        return "x11vnc"
    if "websockify" in cmdline:
        return "websockify"
    if "sandbox_runtime.browser_relay.server" in cmdline:
        return "relay"
    if comm == "Xvfb":
        return "xvfb"
    if "chrom" in comm or "chrome" in cmdline.split(" ", 1)[0]:
        return "chrome"
    return None


def _cpu_quota() -> float | None:
    """Cores this container may use, from cgroup v2; `None` when unlimited."""
    # One exception per clause: this runs on whatever Python the image has,
    # and a formatter targeting 3.14 would unparenthesise a tuple of them.
    try:
        text = Path("/sys/fs/cgroup/cpu.max").read_text()
    except OSError:
        return None
    try:
        quota, period = text.split()
    except ValueError:
        return None
    return None if quota == "max" else int(quota) / int(period)


def stats() -> dict[str, object]:
    groups: dict[str, dict[str, float]] = {}
    me = os.getpid()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "stat").read_text()
            cmdline = (
                (entry / "cmdline")
                .read_bytes()
                .replace(b"\0", b" ")
                .decode(errors="replace")
            )
        except OSError:
            continue
        # comm is parenthesised and may contain spaces; fields resume after ")".
        comm = raw[raw.index("(") + 1 : raw.rindex(")")]
        fields = raw[raw.rindex(")") + 2 :].split()
        group = "probe" if int(entry.name) == me else _process_group(comm, cmdline)
        if group is None:
            continue
        ticks = int(fields[11]) + int(fields[12])  # utime + stime
        rss_pages = int(fields[21])
        slot = groups.setdefault(group, {"ticks": 0, "rss_bytes": 0, "processes": 0})
        slot["ticks"] += ticks
        slot["rss_bytes"] += rss_pages * os.sysconf("SC_PAGE_SIZE")
        slot["processes"] += 1
    cpu_line = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
    jiffies = [int(value) for value in cpu_line]
    return {
        "now_ms": time.time() * 1000,
        "clk_tck": os.sysconf("SC_CLK_TCK"),
        "cpu_count": os.cpu_count(),
        "cpu_quota": _cpu_quota(),
        "machine": {"total": sum(jiffies), "idle": jiffies[3] + jiffies[4]},
        "groups": groups,
        "page": state["report"],
        "page_report_age_ms": (
            None
            if state["report_at"] is None
            else (time.time() - float(state["report_at"])) * 1000  # type: ignore[arg-type]
        ),
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, value: object, status: int = 200) -> None:
        self._send(status, json.dumps(value).encode(), "application/json")

    def do_GET(self) -> None:
        path = urlsplit(self.path).path.rstrip("/") or "/"
        if path == "/":
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif path == "/now":
            self._json({"now_ms": time.time() * 1000})
        elif path == "/mode":
            self._json({"mode": state["mode"]})
        elif path == "/stats":
            self._json(stats())
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        parts = urlsplit(self.path)
        path = parts.path.rstrip("/")
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        if path == "/mode":
            wanted = (parse_qs(parts.query).get("m") or [""])[0]
            if wanted not in MODES:
                self._json({"error": f"mode must be one of {MODES}"}, 400)
                return
            state["mode"] = wanted
            self._json({"mode": wanted})
        elif path == "/open":
            # The browser on screen retires after agent-browser's idle timeout
            # when nobody is watching, and a viewer arriving starts a fresh one
            # on a blank page -- so the runner puts the probe back each time.
            port = self.server.server_address[1]
            opened = subprocess.run(
                ["agent-browser", "open", f"http://127.0.0.1:{port}/"],
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
            self._json(
                {"exit_code": opened.returncode, "output": opened.stdout[-500:] + opened.stderr[-500:]},
                200 if opened.returncode == 0 else 502,
            )
        elif path == "/report":
            try:
                state["report"] = json.loads(body or b"null")
                state["report_at"] = time.time()
            except ValueError:
                self._json({"error": "not json"}, 400)
                return
            self._json({"ok": True})
        else:
            self._json({"error": "not found"}, 404)


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()  # noqa: S104 - sandbox-internal bench server


if __name__ == "__main__":
    main()
