# lemma-stack

`lemma-stack` is the CLI and compatibility library for Lemma Local.

For normal macOS and Windows installs, use the signed Lemma Desktop online or
offline package. Desktop owns the app-private VZ/WSL runtime and no Docker,
Podman, Homebrew, Python, or Node installation is required. Once installed,
these commands automatically discover the signed `lemma-locald` binary and use
the same versioned local control API as Desktop:

```text
lemma-stack start
lemma-stack stop [--infra]
lemma-stack restart
lemma-stack status [--json]
lemma-stack doctor [--json]
lemma-stack logs locald|backend|frontend [-f]
lemma-stack config list|get|set|unset|path
lemma-stack self info [--json]
```

The CLI can start the durable daemon while Desktop is closed. It discovers the
active immutable host/guest release from Desktop configuration and passes only
explicit runtime paths. Set `LEMMA_LOCALD_BIN` for an enterprise/custom binary
location. `LEMMA_STACK_FORCE_EXTERNAL_RUNTIME=1` is the explicit escape hatch
for an existing Docker/Podman install on a machine that also has managed Lemma.

Managed configuration commands read and apply the same transactional schema as
Control Center. Dotted secret keys such as `ai.api_key` are write-only: the CLI
can report only whether a vault value exists. A successful apply restarts and
health-gates the backend while keeping the frontend running, and rolls back on
failure. For example:

```bash
lemma-stack config set \
  ai.protocol=openai_compat \
  ai.base_url=http://127.0.0.1:11434/v1 \
  ai.default_model=qwen3 \
  ai.api_key=lemma-local
```

Managed local endpoints are:

- frontend: `http://app.lemma.localhost:3711`;
- backend API: `http://api.lemma.localhost:8711`;
- built pod apps: `http://<slug>.apps.lemma.localhost:8711`;
- live workspace apps:
  `http://<sandbox>-<app>.workspaces.lemma.localhost:8711`.

The backend is one process containing API, worker, scheduler, AgentBox manager,
surface receivers, and in-process MarkItDown conversion. AgentBox durable state
uses the `agentbox` database in the shared PostgreSQL instance. There is no
separate AgentBox or Kreuzberg service. Sandbox API callbacks come from explicit
`WORKSPACE_CALLBACK_*` configuration; the backend does not infer runtime
topology or rewrite `localhost`.

## External-runtime compatibility

The legacy installer remains available for development, CI, Linux, and
advanced adoption/migration. It requires Docker or Podman and stores state under
`~/.lemma/local`:

```bash
curl -fsSL https://raw.githubusercontent.com/lemma-work/lemma-platform/main/install.sh | bash
lemma-stack install --runtime docker
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

## Development

```bash
cd lemma-stack
uv sync
uv run pytest tests/
uv run ruff check lemma_stack tests
```

`LEMMA_STACK_ROOT` overrides the external-runtime state root.
`LEMMA_STACK_RELEASE_URL` overrides the compatibility release manifest.
