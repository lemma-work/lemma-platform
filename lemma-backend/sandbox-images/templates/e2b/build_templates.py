from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from e2b import Template, default_build_logger, wait_for_port


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
UV_VERSION = "0.11.31"
UV_LINUX_X64_SHA256 = "8cc1cd82d434ec565376f98bd938d4b715b5791a80ff2d3aa78821cf85091b4b"
NODE_VERSION = "24.18.0"
NODE_LINUX_X64_SHA256 = (
    "55aa7153f9d88f28d765fcdad5ae6945b5c0f98a36881703817e4c450fa76742"
)
PNPM_VERSION = "11.15.1"
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
            f"{_install_uv_command()}",
            user="root",
        )
        .copy("lemma-python", "/build/lemma-python")
        .copy("lemma-pod-bundle", "/build/lemma-pod-bundle")
        .copy("lemma-cli", "/build/lemma-cli")
        .copy("lemma-skills", "/build/lemma-skills")
        .copy(
            "agentbox/templates/workspace-python",
            "/build/agentbox/templates/workspace-python",
        )
        .run_cmd(
            "UV_PYTHON_INSTALL_DIR=/opt/python uv python install 3.14 && "
            "UV_PYTHON_INSTALL_DIR=/opt/python "
            "UV_PROJECT_ENVIRONMENT=/opt/agentbox-python "
            "UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy "
            "uv sync --project /build/agentbox/templates/workspace-python "
            "--python 3.14 "
            "--locked --no-dev --no-editable && "
            "printf '%s\\n' "
            "'import sys; "
            'p="/workspace/.python/lib/python3.14/site-packages"; '
            "sys.path.insert(0, p) if p not in sys.path else None' "
            "> /opt/agentbox-python/lib/python3.14/site-packages/"
            "agentbox-workspace-overlay.pth && "
            "test -x /opt/agentbox-python/bin/python && "
            'test "$(/opt/agentbox-python/bin/python -c '
            "'import sys; print(f\"{sys.version_info.major}.{sys.version_info.minor}\")'"
            ')" = "3.14" && '
            "/opt/agentbox-python/bin/python -c "
            '"import ipykernel, lemma_sdk, pydantic" && '
            "test -x /opt/agentbox-python/bin/lemma && "
            "ln -sf /opt/agentbox-python/bin/lemma /usr/local/bin/lemma && "
            "/usr/local/bin/lemma --version && "
            "mkdir -p /root/.local/share/jupyter/kernels/python3 && "
            "printf '%s\\n' "
            '\'{"argv":["/opt/agentbox-python/bin/python","-m",'
            '"ipykernel_launcher","-f","{connection_file}"],'
            '"display_name":"Python 3.14","language":"python",'
            '"metadata":{"debugger":true}}\' '
            "> /root/.local/share/jupyter/kernels/python3/kernel.json && "
            "uv cache clean && "
            "rm -rf /build/lemma-python /build/lemma-pod-bundle "
            "/build/lemma-cli /build/lemma-skills /build/agentbox",
            user="root",
        )
        .copy(
            "agentbox/templates/workspace-node",
            "/opt/agentbox-node",
        )
        .run_cmd(
            "export PATH=/opt/node24/bin:$PATH && "
            "cd /opt/agentbox-node && "
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
            "agentbox/scripts/agentbox-node-tool",
            "/usr/local/lib/agentbox-node-tool",
            mode=0o755,
        )
        .run_cmd(
            "ln -sf /usr/local/lib/agentbox-node-tool "
            "/usr/local/bin/agent-browser && "
            "ln -sf /usr/local/lib/agentbox-node-tool /usr/local/bin/lit && "
            "ln -sf /usr/local/lib/agentbox-node-tool "
            "/usr/local/bin/liteparse && "
            "ln -sf /usr/local/lib/agentbox-node-tool /usr/local/bin/pnpm",
            user="root",
        )
        .run_cmd(
            "AGENTBOX_NODE_BINARY=/opt/node24/bin/node agent-browser install",
            user="user",
        )
        .copy(
            "agentbox/templates/workspace-node/agentbox-profile.sh",
            "/etc/profile.d/agentbox-node.sh",
            mode=0o644,
        )
        .copy(
            "agentbox/templates/workspace-python/agentbox-profile.sh",
            "/etc/profile.d/agentbox-python.sh",
            mode=0o644,
        )
        .copy(
            "agentbox/scripts/start-browser.sh",
            "/usr/local/bin/start-browser",
            mode=0o755,
        )
        .copy(
            "agentbox/scripts/save-webpage.sh",
            "/usr/local/bin/save-webpage",
            mode=0o755,
        )
        .copy(
            "agentbox/scripts/webpage-to-markdown.mjs",
            "/opt/agentbox-node/webpage-to-markdown.mjs",
            mode=0o755,
        )
        .run_cmd(
            "mkdir -p /workspace /tmp/agentbox-browser/runtime "
            "/tmp/agentbox-browser/profile && "
            "ln -sf /opt/agentbox-node/webpage-to-markdown.mjs "
            "/usr/local/lib/webpage-to-markdown.mjs && "
            "find /home/user/.agent-browser/browsers -type f "
            "-name chrome -perm /111 "
            "-exec ln -sf {} /usr/local/bin/workspace-chrome \\; -quit && "
            "test -x /usr/local/bin/workspace-chrome && "
            "rm -rf /root/.cache/pnpm /root/.local/share/pnpm/store "
            "/home/user/.cache/pnpm /home/user/.local/share/pnpm/store && "
            "chown -R user:user /workspace /tmp/agentbox-browser",
            user="root",
        )
        .set_envs(
            {
                "DISPLAY": ":99",
                "XDG_RUNTIME_DIR": "/tmp/agentbox-browser/runtime",
                "WORKSPACE_XVFB_SCREEN": "1440x960x24",
                "AGENT_BROWSER_CONFIG": "/tmp/agentbox-browser/config.json",
                "AGENT_BROWSER_DASHBOARD_PORT": "4848",
                "AGENT_BROWSER_DASHBOARD_INTERNAL_PORT": "4849",
                "AGENT_BROWSER_EXECUTABLE_PATH": "/usr/local/bin/workspace-chrome",
                "AGENT_BROWSER_PROFILE": "/tmp/agentbox-browser/profile",
                "AGENT_BROWSER_SESSION": "workspace",
                "AGENT_BROWSER_HEADED": "true",
                "AGENTBOX_NODE_BINARY": "/opt/node24/bin/node",
                "NODE_PATH": "/opt/agentbox-node/node_modules",
                "PNPM_HOME": "/home/user/.local/share/pnpm",
                "PIP_PREFIX": "/workspace/.python",
                "PYTHONPATH": (
                    "/workspace/.python/lib/python3.14/site-packages:"
                    "/opt/agentbox-python/lib/python3.14/site-packages"
                ),
                "PATH": (
                    "/workspace/.python/bin:/opt/agentbox-python/bin:"
                    "/opt/node24/bin:"
                    "/usr/local/bin:/usr/bin:/bin"
                ),
                "MPLBACKEND": "Agg",
            }
        )
        .set_workdir("/workspace")
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
            "agentbox/templates/function-python",
            "/build/agentbox/templates/function-python",
        )
        .run_cmd(
            f"{_install_uv_command()} && "
            "UV_PROJECT_ENVIRONMENT=/opt/agentbox-function "
            "UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy "
            "uv sync --project /build/agentbox/templates/function-python "
            "--locked --no-dev --no-editable && "
            "uv cache clean && "
            "rm -rf /build/lemma-python /build/agentbox",
            user="root",
        )
        .copy("agentbox/agentbox/__init__.py", "/app/agentbox/__init__.py")
        .copy(
            "agentbox/agentbox/event_catalog.py",
            "/app/agentbox/event_catalog.py",
        )
        .copy(
            "agentbox/agentbox/observability.py",
            "/app/agentbox/observability.py",
        )
        .copy(
            "agentbox/agentbox/function_runtime",
            "/app/agentbox/function_runtime",
        )
        .copy(
            "agentbox/scripts/lemma-function-runtime",
            "/usr/local/bin/lemma-function-runtime",
            mode=0o755,
        )
        .run_cmd("python -m compileall -q /app/agentbox", user="root")
        .set_envs(
            {
                "PYTHONUNBUFFERED": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": "/app",
                "PATH": ("/opt/agentbox-function/bin:/usr/local/bin:/usr/bin:/bin"),
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
            "lemma-agentbox-workspace",
            _positive_int_environment(
                "AGENTBOX_E2B_WORKSPACE_CPU_COUNT",
                default=DEFAULT_CPU_COUNT,
            ),
            _positive_int_environment(
                "AGENTBOX_E2B_WORKSPACE_MEMORY_MB",
                default=DEFAULT_MEMORY_MB,
            ),
        ),
        # The resident runtime imports each immutable revision once and adds
        # workers as concurrent invocations arrive. This is a safety envelope,
        # not an advertised four-request admission limit.
        "function": (
            function_template,
            "lemma-agentbox-function",
            _positive_int_environment(
                "AGENTBOX_E2B_FUNCTION_CPU_COUNT",
                default=DEFAULT_CPU_COUNT,
            ),
            _positive_int_environment(
                "AGENTBOX_E2B_FUNCTION_MEMORY_MB",
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
