from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from e2b import Template, default_build_logger, wait_for_port


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
UV_VERSION = "0.11.31"
UV_LINUX_X64_SHA256 = "8cc1cd82d434ec565376f98bd938d4b715b5791a80ff2d3aa78821cf85091b4b"
NODE_VERSION = "24.18.0"
NODE_LINUX_X64_SHA256 = (
    "55aa7153f9d88f28d765fcdad5ae6945b5c0f98a36881703817e4c450fa76742"
)
PNPM_VERSION = "11.15.1"
GH_VERSION = "2.97.0"
GH_LINUX_X64_SHA256 = "a2c9b8497e1f85b1ad0dfcb78b5a622e098801b8e461e459e88e1ee12f018112"
DEFAULT_CPU_COUNT = 1
DEFAULT_MEMORY_MB = 2048


def _positive_int_environment(name: str, *, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < 1:
        raise ValueError(f"{name} must be at least 1")
    return value


def _install_gh_command() -> str:
    """The GitHub CLI, which Debian does not package.

    An agent working in a repo reaches for `gh` as readily as `git` -- issues,
    PRs and releases have no plumbing equivalent -- and `lemma-github.sh` gives
    it the same credential git already has.
    """

    directory = f"gh_{GH_VERSION}_linux_amd64"
    archive = f"{directory}.tar.gz"
    return (
        f"curl -fsSL https://github.com/cli/cli/releases/download/"
        f"v{GH_VERSION}/{archive} -o /tmp/{archive} && "
        f"echo '{GH_LINUX_X64_SHA256}  /tmp/{archive}' | sha256sum -c - && "
        f"tar -xzf /tmp/{archive} -C /tmp && "
        f"install -m 0755 /tmp/{directory}/bin/gh /usr/local/bin/gh && "
        f"rm -rf /tmp/{archive} /tmp/{directory}"
    )


def _install_uv_command() -> str:
    archive = f"uv-x86_64-unknown-linux-gnu-{UV_VERSION}.tar.gz"
    directory = "uv-x86_64-unknown-linux-gnu"
    return (
        f"curl -fsSL https://github.com/astral-sh/uv/releases/download/"
        f"{UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz "
        f"-o /tmp/{archive} && "
        f"echo '{UV_LINUX_X64_SHA256}  /tmp/{archive}' | sha256sum -c - && "
        f"tar -xzf /tmp/{archive} -C /tmp && "
        f"install -m 0755 /tmp/{directory}/uv /usr/local/bin/uv && "
        f"install -m 0755 /tmp/{directory}/uvx /usr/local/bin/uvx && "
        f"rm -rf /tmp/{archive} /tmp/{directory}"
    )


def workspace_template():
    return (
        Template(file_context_path=REPOSITORY_ROOT)
        .from_template("code-interpreter-v1")
        .apt_install(
            [
                # `agent-browser record` shells out to ffmpeg. See the note in
                # Dockerfile.workspace; the two images have to agree on this or
                # recording works on one fabric and not the other.
                "ffmpeg",
                "fonts-dejavu-core",
                "fonts-liberation",
                "libasound2t64",
                "libatk-bridge2.0-0t64",
                "libatk1.0-0t64",
                "libatspi2.0-0t64",
                "libcairo-gobject2",
                "libcairo2",
                "libcups2t64",
                "libdbus-1-3",
                "libdrm2",
                "libfontconfig1",
                "libfreetype6",
                "libgbm1",
                "libgdk-pixbuf-2.0-0",
                "libgtk-3-0t64",
                "libnspr4",
                "libnss3",
                "libpango-1.0-0",
                "libpangocairo-1.0-0",
                "libx11-6",
                "libx11-xcb1",
                "libxcb-shm0",
                "libxcb1",
                "libxcomposite1",
                "libxcursor1",
                "libxdamage1",
                "libxext6",
                "libxfixes3",
                "libxi6",
                "libxkbcommon0",
                "libxrandr2",
                "libxrender1",
                "libxshmfence1",
                "procps",
                "ripgrep",
                "socat",
                # The human-facing view of the same Xvfb display. See the note
                # in Dockerfile.workspace; the two images have to agree on
                # this or the VNC pane connects on one fabric and not the
                # other -- the same failure mode the `ffmpeg` note above
                # describes, for the same reason.
                # Places the OAuth popup a real sign-in opens, which the X
                # server would otherwise put wherever it liked -- possibly
                # off-screen, where the person watching sees nothing happen.
                "matchbox-window-manager",
                "websockify",
                # `xrandr`, for resizing the display to match the pane it is
                # being watched in.
                "x11-xserver-utils",
                "x11vnc",
                "xz-utils",
                "xvfb",
            ],
            no_install_recommends=True,
        )
        .run_cmd(
            "mkdir -p /opt/node24 && "
            "curl -fsSL "
            f"https://nodejs.org/dist/v{NODE_VERSION}/"
            f"node-v{NODE_VERSION}-linux-x64.tar.xz "
            "-o /tmp/node24.tar.xz && "
            f"echo '{NODE_LINUX_X64_SHA256}  /tmp/node24.tar.xz' "
            "| sha256sum -c - && "
            "tar -xJf /tmp/node24.tar.xz --strip-components=1 "
            "-C /opt/node24 && rm /tmp/node24.tar.xz && "
            "/opt/node24/bin/corepack enable pnpm && "
            f"/opt/node24/bin/corepack prepare pnpm@{PNPM_VERSION} "
            "--activate && "
            f"{_install_uv_command()} && "
            f"{_install_gh_command()}",
            user="root",
        )
        .copy(
            "lemma-backend/sandbox-images/templates/workspace-node",
            "/opt/lemma-node",
        )
        .run_cmd(
            "export PATH=/opt/node24/bin:$PATH && "
            "cd /opt/lemma-node && "
            "pnpm install --prod --frozen-lockfile && "
            "browser_bin_dir=$(find node_modules/.pnpm -type d "
            "-path '*/node_modules/agent-browser/bin' -print -quit) && "
            'test -n "$browser_bin_dir" && '
            'find "$browser_bin_dir" -maxdepth 1 -type f '
            "-name 'agent-browser-*' ! -name agent-browser-linux-x64 "
            "-delete && "
            'chmod 0755 "$browser_bin_dir/agent-browser-linux-x64" && '
            "pnpm store prune",
            user="root",
        )
        .copy(
            "lemma-backend/sandbox-images/scripts/lemma-node-tool",
            "/usr/local/lib/lemma-node-tool",
            mode=0o755,
        )
        .run_cmd(
            "ln -sf /usr/local/lib/lemma-node-tool "
            "/usr/local/bin/agent-browser && "
            "ln -sf /usr/local/lib/lemma-node-tool /usr/local/bin/lit && "
            "ln -sf /usr/local/lib/lemma-node-tool "
            "/usr/local/bin/liteparse && "
            "ln -sf /usr/local/lib/lemma-node-tool /usr/local/bin/pnpm",
            user="root",
        )
        .run_cmd(
            "LEMMA_NODE_BINARY=/opt/node24/bin/node agent-browser install",
            user="user",
        )
        .copy(
            "lemma-backend/sandbox-images/templates/workspace-node/lemma-profile.sh",
            "/etc/profile.d/lemma-node.sh",
            mode=0o644,
        )
        .copy(
            "lemma-backend/sandbox-images/templates/workspace-python/lemma-profile.sh",
            "/etc/profile.d/lemma-python.sh",
            mode=0o644,
        )
        .copy(
            "lemma-backend/sandbox-images/templates/workspace-github/lemma-profile.sh",
            "/etc/profile.d/lemma-github.sh",
            mode=0o644,
        )
        .copy(
            "lemma-backend/sandbox-images/scripts/set-display-size.sh",
            "/usr/local/bin/set-display-size",
            mode=0o755,
        )
        .copy(
            "lemma-backend/sandbox-images/scripts/start-browser.sh",
            "/usr/local/bin/start-browser",
            mode=0o755,
        )
        .copy(
            "lemma-backend/sandbox-images/scripts/start-browser-relay.sh",
            "/usr/local/bin/start-browser-relay",
            mode=0o755,
        )
        # The browser relay, and the package files it needs to be importable.
        #
        # This template deliberately ships no workspace runtime -- an E2B
        # sandbox serves no HTTP of its own, and exec and files go through the
        # provider SDK. The relay is the exception, and it is why it was built
        # as a separate process: a browser channel that lived in the runtime
        # existed on Docker and nowhere else, which is the whole reason this
        # exists.
        #
        # `tasks.py` is here because `browser_relay.app` and
        # `browser_relay.stream_proxy` both import it, and it was not: the
        # comment said "the two package files it needs" while the relay needed
        # three, so every workspace sandbox shipped a relay that raised
        # `ModuleNotFoundError` on its first line and left no log. Counting
        # them by hand is what `test_e2b_templates_ship_what_they_import` now
        # does instead.
        .copy(
            "lemma-backend/sandbox_runtime/__init__.py",
            "/app/sandbox_runtime/__init__.py",
        )
        .copy(
            "lemma-backend/sandbox_runtime/tasks.py",
            "/app/sandbox_runtime/tasks.py",
        )
        .copy(
            "lemma-backend/sandbox_runtime/browser_relay",
            "/app/sandbox_runtime/browser_relay",
        )
        .copy(
            "lemma-backend/sandbox-images/scripts/save-webpage.sh",
            "/usr/local/bin/save-webpage",
            mode=0o755,
        )
        .copy(
            "lemma-backend/sandbox-images/scripts/webpage-to-markdown.mjs",
            "/opt/lemma-node/webpage-to-markdown.mjs",
            mode=0o755,
        )
        # Real Chrome, not the testing build.
        #
        # `workspace-chrome` used to be a symlink to whatever
        # `agent-browser install` had downloaded, and that installer fetches
        # from Google's *Chrome for Testing* CDN. So this fabric ran a build
        # that announces itself in an infobar -- above the page a person is
        # being asked to type their password into -- while the Docker fabric
        # ran ordinary Chromium. Anti-bot systems treat the two differently,
        # which matters most on exactly the sign-in pages this feature exists
        # for.
        #
        # `apt install chromium` is not the answer here the way it is in
        # `Dockerfile.workspace`: this template builds from E2B's Ubuntu-based
        # `code-interpreter-v1`, where `chromium` is the snap transitional
        # package and does not run in a container at all. Google's own apt
        # repository is the one that gives a real, non-testing Chrome on this
        # base.
        .run_cmd(
            "install -d -m 0755 /etc/apt/keyrings && "
            "curl -fsSL https://dl.google.com/linux/linux_signing_key.pub "
            "| gpg --dearmor -o /etc/apt/keyrings/google-chrome.gpg && "
            "chmod a+r /etc/apt/keyrings/google-chrome.gpg && "
            "echo 'deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.gpg] "
            "https://dl.google.com/linux/chrome/deb/ stable main' "
            "> /etc/apt/sources.list.d/google-chrome.list && "
            "apt-get update && "
            "DEBIAN_FRONTEND=noninteractive apt-get install -y "
            "--no-install-recommends google-chrome-stable && "
            "rm -rf /var/lib/apt/lists/*",
            user="root",
        )
        .run_cmd(
            "mkdir -p /home/user /tmp/lemma-browser/runtime "
            "/tmp/lemma-browser/profile && "
            "ln -sf /opt/lemma-node/webpage-to-markdown.mjs "
            "/usr/local/lib/webpage-to-markdown.mjs && "
            'ln -sf "$(command -v google-chrome-stable)" '
            "/usr/local/bin/workspace-chrome && "
            "test -x /usr/local/bin/workspace-chrome && "
            "rm -rf /root/.cache/pnpm /root/.local/share/pnpm/store "
            "/home/user/.cache/pnpm /home/user/.local/share/pnpm/store && "
            "chown -R user:user /home/user /tmp/lemma-browser",
            user="root",
        )
        # Layer order is cache order, and these two blocks were the wrong way
        # round. The first-party sources below -- lemma-cli above all -- change
        # on almost every commit, and they sat in front of the Node layer,
        # whose `pnpm install` fetches the Agent Browser and its Chromium. So a
        # one-line CLI edit invalidated the most expensive layer in the image
        # and rebuilt the browser from scratch. Node first, because its inputs
        # are two lockfiles that move on a dependency bump and nothing else.
        .copy("lemma-python", "/build/lemma-python")
        .copy("lemma-pod-bundle", "/build/lemma-pod-bundle")
        .copy("lemma-cli", "/build/lemma-cli")
        .copy("lemma-skills", "/build/lemma-skills")
        .copy(
            "lemma-backend/sandbox-images/templates/workspace-python",
            "/build/lemma-backend/sandbox-images/templates/workspace-python",
        )
        .run_cmd(
            "UV_PYTHON_INSTALL_DIR=/opt/python uv python install 3.14 && "
            "UV_PYTHON_INSTALL_DIR=/opt/python "
            "UV_PROJECT_ENVIRONMENT=/opt/lemma-python "
            "UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy "
            "uv sync --project /build/lemma-backend/sandbox-images/templates/workspace-python "
            "--python 3.14 "
            "--locked --no-dev --no-editable && "
            "printf '%s\\n' "
            "'import sys; "
            'p="/home/user/.python/lib/python3.14/site-packages"; '
            "sys.path.insert(0, p) if p not in sys.path else None' "
            "> /opt/lemma-python/lib/python3.14/site-packages/"
            "lemma-workspace-overlay.pth && "
            # Where the backend installs the first-party code this image also
            # carries. The copy below is a *floor*, not the shipped version: the
            # overlay supersedes it, so a Lemma code change no longer needs a
            # template at all -- and on a provider where the sandbox is the
            # disk, needing a template meant destroying workspaces to publish
            # one.
            #
            # Both halves are baked, and that is the point. The directory is
            # owned by the sandbox user and the `.pth` is written here, so
            # installing needs no elevation; without them the backend has to
            # reach root to write into `/opt`, which works on E2B only because
            # its user happens to have passwordless sudo.
            #
            # `lemma-runtime-` sorts before `lemma-workspace-` and both insert
            # at position 0, so the later one lands in front: a package the user
            # pip-installs stays ahead of the overlay, and the overlay stays
            # ahead of this image's own copy. Pointing at `current` rather than
            # a version means an upgrade is a symlink flip and never a rewrite
            # of this file. It naming a directory that does not exist yet is
            # harmless -- sys.path tolerates it.
            "mkdir -p /opt/lemma-runtime && "
            "chown user:user /opt/lemma-runtime && "
            "printf '%s\\n' "
            "'import sys; "
            'p="/opt/lemma-runtime/current/site-packages"; '
            "sys.path.insert(0, p) if p not in sys.path else None' "
            "> /opt/lemma-python/lib/python3.14/site-packages/"
            "lemma-runtime-overlay.pth && "
            "test -x /opt/lemma-python/bin/python && "
            'test "$(/opt/lemma-python/bin/python -c '
            "'import sys; print(f\"{sys.version_info.major}.{sys.version_info.minor}\")'"
            ')" = "3.14" && '
            "/opt/lemma-python/bin/python -c "
            '"import ipykernel, lemma_sdk, pydantic" && '
            "test -x /opt/lemma-python/bin/lemma && "
            "ln -sf /opt/lemma-python/bin/lemma /usr/local/bin/lemma && "
            "/usr/local/bin/lemma --version && "
            "mkdir -p /root/.local/share/jupyter/kernels/python3 && "
            "printf '%s\\n' "
            '\'{"argv":["/opt/lemma-python/bin/python","-m",'
            '"ipykernel_launcher","-f","{connection_file}"],'
            '"display_name":"Python 3.14","language":"python",'
            '"metadata":{"debugger":true}}\' '
            "> /root/.local/share/jupyter/kernels/python3/kernel.json && "
            "uv cache clean && "
            "rm -rf /build/lemma-python /build/lemma-pod-bundle "
            "/build/lemma-cli /build/lemma-skills /build/lemma-backend",
            user="root",
        )
        .set_envs(
            {
                "DISPLAY": ":99",
                "XDG_RUNTIME_DIR": "/tmp/lemma-browser/runtime",
                "WORKSPACE_XVFB_SCREEN": "1440x960x24",
                "AGENT_BROWSER_CONFIG": "/tmp/lemma-browser/config.json",
                "AGENT_BROWSER_EXECUTABLE_PATH": "/usr/local/bin/workspace-chrome",
                "AGENT_BROWSER_PROFILE": "/tmp/lemma-browser/profile",
                "AGENT_BROWSER_SESSION": "workspace",
                "AGENT_BROWSER_HEADED": "true",
                # See Dockerfile.workspace: the daemon closes Chrome after this
                # long idle, which is what keeps a finished research session
                # from holding the sandbox's whole memory budget.
                "AGENT_BROWSER_IDLE_TIMEOUT_MS": "120000",
                # See Dockerfile.workspace: the ceiling on a viewer-requested
                # resize, since RandR cannot grow the framebuffer Xvfb
                # allocated at startup.
                "WORKSPACE_XVFB_MAX_SCREEN": "1920x1200x24",
                "LEMMA_BROWSER_RELAY_PORT": "4850",
                "LEMMA_NODE_BINARY": "/opt/node24/bin/node",
                # Where the credential bridge writes gh's config.
                "GH_CONFIG_DIR": "/tmp/lemma-gh",
                "GH_NO_UPDATE_NOTIFIER": "1",
                "GH_PAGER": "cat",
                "NODE_PATH": "/opt/lemma-node/node_modules",
                "PNPM_HOME": "/home/user/.local/share/pnpm",
                "PIP_PREFIX": "/home/user/.python",
                "PYTHONPATH": (
                    "/home/user/.python/lib/python3.14/site-packages:"
                    "/opt/lemma-python/lib/python3.14/site-packages:"
                    # Where the browser relay package lives.
                    "/app"
                ),
                "PATH": (
                    "/home/user/.python/bin:/home/user/.local/share/pnpm:"
                    "/home/user/.local/bin:"
                    "/opt/lemma-python/bin:"
                    "/opt/node24/bin:"
                    "/usr/local/bin:/usr/bin:/bin"
                ),
                "MPLBACKEND": "Agg",
                "UV_CACHE_DIR": "/home/user/.uv-cache",
            }
        )
        .set_workdir("/home/user")
        .set_user("user")
    )


def function_template():
    return (
        Template(file_context_path=REPOSITORY_ROOT)
        .from_python_image("3.14")
        .apt_install(
            ["bash", "ca-certificates", "curl", "procps"],
            no_install_recommends=True,
        )
        .copy("lemma-python", "/build/lemma-python")
        .copy(
            "lemma-backend/sandbox-images/templates/function-python",
            "/build/lemma-backend/sandbox-images/templates/function-python",
        )
        .run_cmd(
            f"{_install_uv_command()} && "
            "UV_PROJECT_ENVIRONMENT=/opt/lemma-function "
            "UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy "
            "uv sync --project /build/lemma-backend/sandbox-images/templates/function-python "
            "--locked --no-dev --no-editable && "
            "uv cache clean && "
            "rm -rf /build/lemma-python /build/lemma-backend",
            user="root",
        )
        .copy(
            "lemma-backend/sandbox_runtime/__init__.py",
            "/app/sandbox_runtime/__init__.py",
        )
        .copy(
            "lemma-backend/sandbox_runtime/tasks.py",
            "/app/sandbox_runtime/tasks.py",
        )
        .copy(
            "lemma-backend/sandbox_runtime/function",
            "/app/sandbox_runtime/function",
        )
        .copy(
            "lemma-backend/sandbox-images/scripts/lemma-function-runtime",
            "/usr/local/bin/lemma-function-runtime",
            mode=0o755,
        )
        .run_cmd("python -m compileall -q /app/sandbox_runtime", user="root")
        .set_envs(
            {
                "PYTHONUNBUFFERED": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": "/app",
                "PATH": ("/opt/lemma-function/bin:/usr/local/bin:/usr/bin:/bin"),
            }
        )
        .set_workdir("/tmp")
        .set_user("user")
        .set_start_cmd(
            "lemma-function-runtime serve --host 0.0.0.0 --port 8090",
            wait_for_port(8090),
        )
    )


def build(
    *,
    target: str,
    name_suffix: str = "",
) -> dict[str, dict[str, str]]:
    if not os.environ.get("E2B_API_KEY"):
        raise RuntimeError("E2B_API_KEY is required")
    selected = {
        "workspace": (
            workspace_template,
            "lemma-workspace",
            _positive_int_environment(
                "E2B_WORKSPACE_CPU_COUNT",
                default=DEFAULT_CPU_COUNT,
            ),
            _positive_int_environment(
                "E2B_WORKSPACE_MEMORY_MB",
                default=DEFAULT_MEMORY_MB,
            ),
        ),
        # The resident runtime imports each immutable revision once and adds
        # workers as concurrent invocations arrive. This is a safety envelope,
        # not an advertised four-request admission limit.
        "function": (
            function_template,
            "lemma-function",
            _positive_int_environment(
                "E2B_FUNCTION_CPU_COUNT",
                default=DEFAULT_CPU_COUNT,
            ),
            _positive_int_environment(
                "E2B_FUNCTION_MEMORY_MB",
                default=DEFAULT_MEMORY_MB,
            ),
        ),
    }
    names = tuple(selected) if target == "all" else (target,)
    result: dict[str, dict[str, str]] = {}
    for name in names:
        factory, base_template_name, cpu_count, memory_mb = selected[name]
        template_name = f"{base_template_name}{name_suffix}"
        built = Template.build(
            factory(),
            template_name,
            cpu_count=cpu_count,
            memory_mb=memory_mb,
            on_build_logs=default_build_logger(),
        )
        result[name] = {
            "template_id": built.template_id,
            "build_id": built.build_id,
            "name": built.name,
            "cpu_count": str(cpu_count),
            "memory_mb": str(memory_mb),
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--target", choices=("workspace", "function", "all"), default="all"
    )
    parser.add_argument(
        "--name-suffix",
        default="",
        help=(
            "Optional suffix for isolated candidate builds; production builds "
            "leave this empty."
        ),
    )
    args = parser.parse_args()
    print(
        json.dumps(
            build(target=args.target, name_suffix=args.name_suffix),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
