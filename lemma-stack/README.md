# lemma-stack

`lemma-stack` is the CLI control surface for local Lemma.

When Lemma Desktop is installed, the CLI discovers its durable
`lemma-locald` daemon and uses the same dynamic ports, lifecycle, configuration
schema, OS credential vault, and private runtime. It does not start a second
stack.

## Managed Desktop commands

```bash
lemma-stack prepare          # one-time Windows runtime preparation
lemma-stack start
lemma-stack restart
lemma-stack stop
lemma-stack stop --infra     # also stop the private VM/WSL distribution
lemma-stack status
lemma-stack status --json
lemma-stack doctor
lemma-stack doctor --json
lemma-stack logs locald
lemma-stack logs backend -f
lemma-stack logs frontend
lemma-stack self info
lemma-stack self register-cli --use
```

The CLI finds Desktop in `/Applications`, `~/Applications`, or the Windows
install locations, reads the installed release identity, and talks to locald
over its authenticated per-user socket/named pipe. Endpoints come from locald
state; managed ports are never fixed CLI defaults.

Managed configuration:

```bash
lemma-stack config list
lemma-stack config get ai.protocol
lemma-stack config set \
  ai.protocol=openai_compat \
  ai.base_url=http://127.0.0.1:11434/v1 \
  ai.default_model=qwen3
lemma-stack config unset ai.protocol
```

Secret values are write-only and stored in Keychain/Credential Manager.
Configuration apply is transactional and includes provider validation plus a
controlled backend restart.

## Managed architecture

The host has exactly one backend process and one frontend process. The backend
contains API, worker, scheduler, sandbox manager, surfaces, and document
processing. PostgreSQL, Redis, SuperTokens, and sandbox containers remain in
one app-owned private Linux runtime.

Closing Desktop does not stop the daemon or schedules. Use
`lemma-stack stop --infra` when all resources should be released.

State roots:

- macOS: `~/Library/Application Support/Lemma/locald`
- Windows: `%LOCALAPPDATA%\Lemma\locald`

## External-runtime compatibility

`lemma-stack install` and container-oriented database commands remain for
explicit Linux/development/migration compatibility. That path may use Docker
or Podman and stores separate state under `~/.lemma/local`.

Desktop never auto-selects or mutates the external runtime. Set
`LEMMA_STACK_FORCE_EXTERNAL_RUNTIME=1` only when intentionally operating that
compatibility installation.

The compatibility path is not the supported macOS/Windows product install and
must not be used to document managed ports, data locations, or lifecycle.
