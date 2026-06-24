# lemma-stack

Installer and management tool for a fully-local Lemma stack.

```bash
# one-line install (from the repo root: ./install.sh)
curl -fsSL https://raw.githubusercontent.com/lemma-work/lemma-platform/main/install.sh | bash
```

`lemma-stack install` detects docker/podman (offering to install podman — the
recommended runtime), pulls the released images, and starts everything with
all persistent state under `~/.lemma/local`:

- frontend: http://127-0-0-1.sslip.io:3711
- backend API: http://127-0-0-1.sslip.io:8711 (docs at /scalar)
- infra (postgres/redis/supertokens/kreuzberg) stays on the private
  `lemma-local-net` container network — no host ports, no collisions.

Use the `127-0-0-1.sslip.io` host (wildcard DNS that resolves to `127.0.0.1`),
not `localhost` / `127.0.0.1` directly: sign-in cookies and per-desk subdomains
are scoped to it, so the app only authenticates on this host.

## Commands

```text
lemma-stack install   [--runtime auto|docker|podman] [--channel stable|X.Y.Z]
                      [--manifest path.json] [--set KEY=VAL ...] [-y]
lemma-stack start | stop [--infra] | restart | status [--json]
lemma-stack logs <service> [-f]
lemma-stack doctor [--json]
lemma-stack config list|get|set|unset|edit|path
lemma-stack db shell|sql|url      lemma-stack redis cli
lemma-stack uninstall [--purge-data]
lemma-stack self version|info
```

Configuration lives in `~/.lemma/local/config.toml`. Bare UPPER_SNAKE keys
route to the backend environment:

```bash
lemma-stack config set LEMMA_ANTHROPIC_API_KEY sk-ant-...
lemma-stack restart
```

## Development

```bash
cd lemma-stack
uv sync
uv run python -m pytest tests/
uv run ruff check lemma_stack tests
```

To exercise an install against locally built images, write a manifest with
your `:local` tags and run
`lemma-stack install --manifest manifest.json --runtime docker -y`.
`LEMMA_STACK_ROOT` overrides the state root (default `~/.lemma/local`);
`LEMMA_STACK_RELEASE_URL` overrides the manifest location.
