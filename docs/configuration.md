# Configuring Lemma

This is the operator's view of Lemma's configuration: the settings you set to
run or deploy it, what each one decides, and why you would change it. It is not
an exhaustive dump of every field — the platform declares a few hundred, most of
which are tuning knobs with defaults that are correct until a specific problem
says otherwise. Those are listed in the settings classes named at the end.

Every setting is an environment variable. Names are the upper-cased field name
of a `pydantic-settings` class, so what you see here is what the process reads.

## Where settings come from

The backend reads its environment, then `lemma-backend/.env` in its working
directory. A real environment variable always wins over the file. Setting
`LEMMA_DISABLE_DOTENV=1` skips the file entirely, which is what the test suite
does so a developer's local `.env` cannot change a test result.

How that environment gets built depends on how you run Lemma:

| How you run it | What writes the environment |
| --- | --- |
| `make dev` from a checkout | `lemma-backend/.env`, created by `make init`, plus the dev overrides in the root `Makefile` |
| `lemma-stack` (Docker or Podman) | `lemma_stack/config/render.py`, from `~/.lemma/local/config.toml` |
| Lemma Desktop | `locald`'s native host pack renderer, from the same config file |
| Your own deployment | Whatever your platform injects |

For the two managed paths you do not edit the environment directly. You edit
`~/.lemma/local/config.toml` — `lemma-stack config set KEY value` — and the
renderer produces the environment from it. Anything under `[backend.env]` is
passed to the backend verbatim and applied last, so it overrides a rendered
default. `[frontend.env]` does the same for the frontend. Configs written before
sandbox provisioning moved into the backend may also carry an `[agentbox.env]`
section; it is still read, and lands in the same backend environment, with
`[backend.env]` winning on a conflict.

## Runtime and logging

```dotenv
# local | development | production | testing. Outside local/testing, some
# settings stop having safe defaults and are required — APP_BASE_DOMAIN is one.
ENVIRONMENT=production
DEBUG=false
LOG_LEVEL=INFO
# Structured JSON on stdout. Turn it off locally if you read logs by eye.
JSON_LOGS_ENABLED=true
# Per-request access logs. Noisy in production, useful in a checkout.
LOCAL_HTTP_ACCESS_LOGS_ENABLED=false
```

## Database and Redis

Lemma uses two Postgres databases: the application database and a separate
datastore database holding pod data, which is queried under row-level security
by a lower-privilege role. Redis carries the event streams and caches.

```dotenv
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/lemma
DATASTORE_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/lemma_datastore
REDIS_URL=redis://localhost:6379

# Pool sizing. The ceiling that matters is POSTGRES_MAX_CONNECTIONS: every API
# and worker process opens up to DB_POOL_SIZE + DB_MAX_OVERFLOW, so the product
# across all replicas has to stay under what the server allows.
DB_POOL_SIZE=10
DB_MAX_OVERFLOW=10
POSTGRES_MAX_CONNECTIONS=100
WORKER_CONCURRENCY=20
```

## Sandboxes

Agent workspaces and function runs both execute in sandboxes that the backend
provisions itself. `WORKSPACE_PROVIDER` chooses what a sandbox is made of:

| Value | Sandbox is | Where the files live |
| --- | --- | --- |
| `docker` (default) | A container on a Docker or Podman socket | A separate volume, kept when the container is replaced |
| `e2b` | An E2B sandbox | The sandbox itself — there is no volume behind it |
| `lemma_local` | A container inside Lemma Desktop's private VM | A bind mount inside the guest |

That difference decides what happens when a sandbox has to be recreated. On
Docker and `lemma_local` the compute is replaced and the files survive. On E2B
the sandbox *is* the disk, so recreating one loses its contents; the backend
reports this so the user can be told rather than silently handed an empty
workspace.

### What each provider actually reads

`WorkspaceSettings` declares every field regardless of provider, so a value
being *set* does not mean it is *used*. Setting one the active provider ignores
is harmless, but it is not a substitute for the one that matters.

| Setting | `docker` | `e2b` | `lemma_local` |
| --- | --- | --- | --- |
| `WORKSPACE_IMAGE` / `FUNCTION_IMAGE` | **required** | not used | **required** |
| `E2B_API_KEY`, `E2B_WORKSPACE_TEMPLATE`, `E2B_FUNCTION_TEMPLATE` | not used | **required** | not used |
| `WORKSPACE_PROFILE_DIGEST` / `FUNCTION_PROFILE_DIGEST` | **used** | **used** | **used** |
| `WORKSPACE_RUNTIME_CREDENTIAL_KEY` | **required** | **required** | **required** |
| `WORKSPACE_DOCKER_*`, `WORKSPACE_ADD_HOST_GATEWAY`, `WORKSPACE_HOST_ALIAS` | **used** | not used | not used |
| `WORKSPACE_LOCAL_*` | not used | not used | **required** |

**Under `e2b`, the images are not what a sandbox is made from — the templates
are.** `E2BSandboxProvider.create` passes `template=...` and never reads the
image, so leaving `WORKSPACE_IMAGE` at its default is correct there. What
still matters on E2B is the profile digest: it is stamped into sandbox
metadata and is the only thing that moves an existing workspace onto a
rebuilt template.

```dotenv
WORKSPACE_PROVIDER=docker

# Docker and lemma_local only. Pin by digest in any real deployment;
# WORKSPACE_DOCKER_ALLOW_MUTABLE_IMAGES=false refuses a tag that is not pinned.
WORKSPACE_IMAGE=ghcr.io/lemma-work/lemma-workspace@sha256:...
FUNCTION_IMAGE=ghcr.io/lemma-work/lemma-function@sha256:...

# Signs the per-sandbox credential the in-sandbox runtime accepts. At least 32
# bytes. Required for any provider that runs a workspace runtime.
WORKSPACE_RUNTIME_CREDENTIAL_KEY=...

# Release an idle workspace after this long. The sweep that enforces it runs on
# WORKSPACE_SWEEP_CRON. Releasing keeps the files; it stops the compute.
WORKSPACE_IDLE_RELEASE_SECONDS=900
WORKSPACE_SWEEP_CRON=*/5 * * * *
```

### Making a new sandbox image take effect

A sandbox is reused only when the profile digest recorded on it matches
`WORKSPACE_PROFILE_DIGEST` (or `FUNCTION_PROFILE_DIGEST` for function runtimes).
This is the supported way to force existing workspaces onto a new image:
publish the image, point `WORKSPACE_IMAGE` at it, and bump the digest in the
same change. Without the bump, a workspace that already exists keeps running the
image it was created from for as long as it lives, and a fix shipped in the
image never reaches anyone who already has a workspace.

The digest is an opaque identity — any `sha256:` value works, as long as it
changes when the image does.

```dotenv
WORKSPACE_PROFILE_NAME=workspace-python-v1
WORKSPACE_PROFILE_DIGEST=sha256:<64 hex characters>
FUNCTION_PROFILE_NAME=function-python-v1
FUNCTION_PROFILE_DIGEST=sha256:<64 hex characters>
```

### Docker and Podman

```dotenv
WORKSPACE_DOCKER_SOCKET_PATH=/var/run/docker.sock
# Put sandboxes on a private network the backend also joins, so they reach it
# by DNS alias instead of through the host.
WORKSPACE_DOCKER_PRIVATE_NETWORK=lemma-local-net
# Refuse an image that is not pinned by digest. Only relax this in a checkout,
# where the dev images are tagged :dev.
WORKSPACE_DOCKER_ALLOW_MUTABLE_IMAGES=false
# When sandboxes are NOT on a shared network, they need a route back to the
# host. Both are set together: an alias without the gateway entry provisions
# fine and then fails on the sandbox's first call back.
WORKSPACE_ADD_HOST_GATEWAY=true
WORKSPACE_HOST_ALIAS=host.docker.internal
```

### E2B

```dotenv
E2B_API_KEY=...
E2B_WORKSPACE_TEMPLATE=lemma-workspace
E2B_FUNCTION_TEMPLATE=lemma-function
# Only for a self-hosted or non-default E2B deployment.
E2B_DOMAIN=
```

These four are the whole backend-side E2B surface. In particular:

- **`E2B_WORKSPACE_BUILD_ID` and `E2B_FUNCTION_BUILD_ID` are not backend
  settings.** `WorkspaceSettings` does not declare them and the backend never
  reads them. They are GitHub Actions repository variables, consumed by the
  E2B conformance and function-benchmark workflows to pin the exact template
  build those runs exercise. Setting them in a deployment environment does
  nothing; do not treat a template id alone as an unpinned deployment.
- A template name is a moving pointer: rebuilding a template under the same
  name changes what a *new* sandbox is made from. It does not touch sandboxes
  that already exist — bump `WORKSPACE_PROFILE_DIGEST` for that.

### Reaching a sandbox

Sandboxes call back into Lemma — the CLI inside a workspace, a function
fetching its artifact. These URLs are what they are told to use, and they are
resolved from the sandbox's network position, not the browser's.

```dotenv
WORKSPACE_CALLBACK_API_URL=http://backend:8000
WORKSPACE_CALLBACK_AUTH_URL=http://frontend:8080/auth
WORKSPACE_CALLBACK_FRONTEND_URL=http://frontend:8080
FUNCTION_RUNTIME_GATEWAY_URL=http://backend:8000
```

`WORKSPACE_PORT_ACCESS_URL` publishes a port a workspace opened, for previewing
something running inside it.

## Function execution

```dotenv
# How long an API-style call and a job-style run may take before they are cut off.
FUNCTION_API_DEADLINE_SECONDS=120
FUNCTION_JOB_DEADLINE_SECONDS=600
# Reuse a resolved runtime endpoint for this long instead of re-resolving per
# call. Keep it well below WORKSPACE_IDLE_RELEASE_SECONDS, or a cached endpoint
# can outlive the sandbox it points at.
FUNCTION_RUNTIME_ENDPOINT_REUSE_SECONDS=60
```

## URLs, CORS and cookies

`API_URL` and `FRONTEND_URL` are what a browser uses, so they must be the public
origins, not internal service names. Apps that pods publish are served at
`<slug>.<APP_BASE_DOMAIN>`, which is required outside `local` and `testing`.

```dotenv
API_URL=https://api.example.com
FRONTEND_URL=https://app.example.com
AUTH_FRONTEND_URL=https://app.example.com
APP_BASE_DOMAIN=apps.example.com
SUPERTOKENS_CORE_URL=http://supertokens:3567

CORS_ORIGINS=["https://app.example.com"]
CORS_ORIGIN_REGEX=
# Leave the domain blank for a host-only cookie. Set it only when the UI and API
# are on different subdomains that must share a session.
SESSION_COOKIE_DOMAIN=
SESSION_COOKIE_SECURE=true
SESSION_COOKIE_SAME_SITE=lax
```

## Authentication and email

Email transport, sender identity, and the sign-up abuse controls are covered in
[authentication hardening](authentication-hardening.md), which documents the
`AUTH_*`, `SMTP_*` and `RESEND_*` settings together with the reasoning behind
each default. The short version:

```dotenv
EMAIL_TRANSPORT=resend        # smtp | resend | filesystem
EMAIL_OUTPUT_DIR=/tmp/lemma-emails   # filesystem transport only
AUTH_EMAIL_VERIFICATION_REQUIRED=true
AUTH_ABUSE_PROTECTION_ENABLED=true
```

## Storage

Object storage holds uploads and generated artifacts. `auto` picks local disk
when no bucket is configured. See
[object storage](../lemma-backend/docs/operators/object-storage.md) for the
per-cloud credentials.

```dotenv
STORAGE_BACKEND=auto          # auto | local | gcs | s3 | azure
STORAGE_BUCKET=
LOCAL_OBJECT_STORAGE_ROOT=/var/lib/lemma/object-storage
LOCAL_FILE_STORAGE_ROOT=/var/lib/lemma/files
```

## Secret encryption

Connector credentials and other stored secrets are encrypted at rest. The
provider decides where the key comes from; `auto` uses an explicit key if one is
set and falls back to a local key otherwise.

```dotenv
SECRET_KEY_PROVIDER=auto      # auto | env | gcp_kms | gcp_secret_manager | keychain
SECRET_ENCRYPTION_KEY=
GCP_KMS_KEY_NAME=
```

Rotating or losing this key makes every encrypted row unreadable. Treat it as
durable state, not configuration.

## Models

Lemma talks to any OpenAI-compatible or Anthropic-compatible endpoint. There is
no provider-specific logic beyond those two shapes.

```dotenv
LEMMA_DEFAULT_MODEL_TYPE=openai_compat   # openai_compat | anthropic_compat
LEMMA_OPENAI_API_KEY=
LEMMA_OPENAI_BASE_URL=https://api.openai.com/v1
LEMMA_OPENAI_DEFAULT_MODEL=
# Comma-separated. The vision list marks which of them accept images.
LEMMA_OPENAI_MODEL_NAMES=
LEMMA_OPENAI_VISION_MODEL_NAMES=

LEMMA_ANTHROPIC_API_KEY=
LEMMA_ANTHROPIC_DEFAULT_MODEL=claude-sonnet-4-5

# Embeddings and reranking for datastore search. `local` runs in-process and
# needs no key; the dimension must match what your index was built with.
EMBEDDING_PROVIDER=auto       # auto | local | openai_compat
EMBEDDING_DIMENSION=768
RERANKER_MODE=off             # off | local | openai_compat

WEB_SEARCH_PROVIDER=auto      # auto | duckduckgo | searxng | brave
BRAVE_SEARCH_API_KEY=
SEARXNG_URL=
```

## Document processing

```dotenv
DOCUMENT_PROCESSOR=markitdown # markitdown | docling | kreuzberg
DOCUMENT_PROCESSING_OCR_ENABLED=false
DOCUMENT_PROCESSING_MAX_FILE_BYTES=
DOCUMENT_PROCESSING_MAX_CONCURRENCY=
```

`markitdown` runs in-process. `docling` and `kreuzberg` are HTTP services and
need `DOCLING_SERVE_URL` or `KREUZBERG_URL` respectively.

## Observability

Off by default. [Observability](observability.md) documents the full OTel
surface, including the separate `LLM_OTEL_*` pipeline for model-call traces.

```dotenv
OBSERVABILITY_ENABLED=true
OTEL_EXPORTER_OTLP_ENDPOINT=http://collector:4317
OTEL_SERVICE_NAME=lemma-backend
OTEL_TRACES_SAMPLER_ARG=0.05
```

## Chat surfaces

Each surface needs its own credentials, and none is required — a surface with no
token is simply inactive. Local installs have no public URL, so they receive
events by polling or socket instead of webhooks.

```dotenv
SLACK_BOT_TOKEN=
SLACK_SIGNING_SECRET=
ENABLE_SLACK_SOCKET_MODE=false

TELEGRAM_BOT_TOKEN=
TELEGRAM_WEBHOOK_SECRET=
ENABLE_TELEGRAM_POLLING_MODE=false

WHATSAPP_ACCESS_TOKEN=
MICROSOFT_BOT_APP_ID=
```

## Frontend

The frontend reads `NEXT_PUBLIC_*` variables, which are applied at runtime.
`lemma-frontend/.env.example` is the working list.

```dotenv
NEXT_PUBLIC_API_URL=https://api.example.com
NEXT_PUBLIC_SITE_URL=https://app.example.com
NEXT_PUBLIC_AUTH_URL=https://app.example.com
NEXT_PUBLIC_APPS_DOMAIN_SUFFIX=apps.example.com
```

## Container runtime selection

`LEMMA_CONTAINER_RUNTIME` selects which container CLI the local stack drives —
`docker`, `podman`, `lemma_local`, or `auto` to detect. It is read by Lemma
Desktop and `lemma-stack`, not by the backend, and it is not the same thing as
`WORKSPACE_PROVIDER`: the stack derives that from this, and the two accept
different values.

## Everything else

Settings not covered here are declared in these classes. Each field carries a
description, a default, and the validation that applies to it, which is the
authoritative answer for anything this document does not name.

| Area | Class |
| --- | --- |
| Core runtime, database, URLs, storage, models, observability | `lemma-backend/app/core/config.py` |
| Sandboxes and function runtimes | `lemma-backend/app/modules/workspace/config.py` |
| Agents | `lemma-backend/app/modules/agent/config.py` |
| Chat surfaces | `lemma-backend/app/modules/agent_surfaces/config.py` |
| Connectors | `lemma-backend/app/modules/connectors/config.py` |
| Datastore and document processing | `lemma-backend/app/modules/datastore/config.py` |
| Pod bundles | `lemma-backend/app/modules/pod_bundle/config.py` |
| Apps, icons, schedules | `app/modules/{apps,icon,schedule}/config.py` |
| Event transport | `lemma-backend/app/core/infrastructure/events/config.py` |

Settings whose description begins with `TEST HOOK ONLY` exist for the end-to-end
suite and should never be set in a real deployment.
