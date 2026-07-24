# lemma-stack

`lemma-stack` is the CLI and compatibility library for Lemma Local.

For normal macOS and Windows installs, use the signed Lemma Desktop online or
offline package. Desktop owns the app-private VZ/WSL runtime and no Docker,
Podman, Homebrew, Python, or Node installation is required. Once installed,
these commands automatically discover the signed `lemma-locald` binary and use
the same versioned local control API as Desktop:

```text
lemma-stack start
lemma-stack prepare
lemma-stack stop [--infra]
lemma-stack restart
lemma-stack status [--json]
lemma-stack doctor [--json]
lemma-stack logs locald|backend|frontend [-f]
lemma-stack config list|get|set|unset|path
lemma-stack self info [--json]
lemma-stack self register-cli [--use|--no-use]
```

Install the optional control CLI after completing Desktop Local setup once:

```bash
curl -fsSL https://raw.githubusercontent.com/lemma-work/lemma-platform/main/install.sh |
  bash -s -- --cli-only
```

On PowerShell, set `LEMMA_STACK_CLI_ONLY=1` while invoking `install.ps1`. The
bootstrap installs this tool from the repository (it is not on PyPI yet) and
registers the managed origins as the Lemma pod CLI server named `local`.

The CLI can start the durable daemon while Desktop is closed. It discovers the
active immutable host/guest release from Desktop configuration and passes only
explicit runtime paths. Set `LEMMA_LOCALD_BIN` for an enterprise/custom binary
location. `LEMMA_STACK_FORCE_EXTERNAL_RUNTIME=1` is the explicit escape hatch
for an existing Docker/Podman install on a machine that also has managed Lemma.
On Windows, `lemma-stack prepare` is the explicit one-time WSL2 enablement
action. Ordinary `start` remains unprivileged and tells the caller whether
preparation or a Windows restart is required.

Managed configuration commands read and apply the same transactional schema as
Control Center. Dotted secret keys such as `ai.api_key` are write-only: the CLI
can report only whether a vault value exists. A successful apply restarts and
health-gates the backend while keeping the frontend running, and rolls back on
failure. For example:

```bash
lemma-stack config set \
  ai.protocol=openai_compat \
  ai.base_url=http://127.0.0.1:11434/v1 \
  ai.default_model=qwen3
```

Managed Desktop chooses a high frontend/backend port pair on first start,
persists it in its private `network.json`, and publishes the resolved origins
through locald status. **Local Control Center → Diagnostics** displays the exact
workspace, API, built-app, live-workspace, and OAuth callback URLs. The
`lemma-stack status --json` command returns `url` and `api_url`; the `lemma`
CLI discovers those values automatically when the `local` server is selected.
If another application later owns either persisted port, locald safely
allocates a new pair without terminating the other process.

The backend is one process containing API, worker, scheduler, AgentBox manager,
surface receivers, and in-process MarkItDown conversion. AgentBox durable state
uses the `agentbox` database in the shared PostgreSQL instance. There is no
separate AgentBox or Kreuzberg service. Sandbox API callbacks come from explicit
`WORKSPACE_CALLBACK_*` and `FUNCTION_RUNTIME_GATEWAY_URL` configuration; the
backend does not infer runtime topology or rewrite `localhost`.

## External-runtime compatibility

The legacy installer remains available for development, CI, Linux, and
advanced adoption/migration. It requires Docker or Podman and stores state under
`~/.lemma/local`:

```bash
curl -fsSL https://raw.githubusercontent.com/lemma-work/lemma-platform/main/install.sh | bash
```

```text
lemma-stack install [--runtime auto|docker|podman] [--channel stable|X.Y.Z]
                    [--manifest path.json] [--set KEY=VAL ...] [-y]
lemma-stack config list|get|set|unset|edit|path
lemma-stack db shell|sql|url
lemma-stack redis cli
lemma-stack uninstall [--purge-data]
```

This compatibility path is never auto-selected by Desktop and never modifies
the user's default Docker context or Podman connection. Its configuration lives
in `~/.lemma/local/config.toml`; managed Desktop secrets instead live in the OS
credential vault and are changed through Control Center.

`db`, `redis`, and `uninstall` are external-runtime-only commands. Removing
managed Desktop or its data is not delegated to those container commands.

The complete user guide is
[Install and run Lemma Local](../docs/installation.md).

## Development

```bash
cd lemma-stack
uv sync
uv run pytest tests/
uv run ruff check lemma_stack tests
```

`LEMMA_STACK_ROOT` overrides the external-runtime state root.
`LEMMA_STACK_RELEASE_URL` overrides the compatibility release manifest.
