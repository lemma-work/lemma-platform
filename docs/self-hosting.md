# Self-hosting Lemma on a VM

One `docker-compose.yml`, on one machine you control. This is the path for
running Lemma for a team on a VPS, a cloud VM, or a box in a cupboard.

If you want Lemma on your own laptop for yourself, install
[Lemma Desktop](installation.md) instead — it needs no Docker, no Postgres, and
no reverse proxy.

Everything below lives in [`deploy/compose/`](../deploy/compose).

## What you need

- A Linux machine with **4 vCPU and 8 GB of RAM**, and 40 GB of disk. Images
  and volumes are about 6.6 GB of that before you store anything; the rest is
  headroom for your data and for the sandbox containers agents run in.
- **Docker Engine 25+ with the Compose v2 plugin.** Not Docker Desktop — this
  is a server.
- `curl` and `python3`, which every mainstream server image already has.
- An **API key for a model provider**. Agents do not work without one, and
  nothing here supplies it. Any OpenAI-compatible or Anthropic-compatible
  endpoint works, including a gateway or a local model you run yourself.

A domain is optional. Without one you get a working, encrypted deployment with
a browser certificate warning; see [Using your own domain](#using-your-own-domain).

## Install

```bash
git clone https://github.com/lemma-work/lemma-platform.git
cd lemma-platform/deploy/compose
./bootstrap.sh
```

`bootstrap.sh` writes `.env`: the hostname to serve on, freshly generated
secrets, and image references pinned by digest from the latest published
release. It does not start anything, and it will not overwrite an existing
`.env`.

Open `.env`, set a model provider key, then:

```bash
docker compose up -d
```

First start downloads about 1.5 GB of compressed images, which unpack to
roughly 6 GB on disk, and runs the database migrations. The agent workspace
image is 2.5 GB of that on its own — it carries Chromium, Node, Python, and the
Lemma SDKs, because that is the machine your agents get. When
`docker compose ps` shows `api` healthy, open the URL `bootstrap.sh` printed
and create the first account.

## What is running

| Container | What it is |
|---|---|
| `caddy` | TLS and routing. Optional — see [Bring your own load balancer](#bring-your-own-load-balancer). |
| `frontend` | The Next.js UI. |
| `api` | HTTP, WebSockets, authorization, durable writes. |
| `worker` | Agent runs, indexing, surface ingest, scheduled work. |
| `db` | Postgres with pgvector. Three databases: `lemma`, `lemma_datastore`, `supertokens`. |
| `redis` | Job queues and cache. |
| `supertokens` | The authentication core. |
| `migrate` | Runs once per `up`, applies migrations, exits. |
| `sandbox-images` | Runs once per `up`, pulls the two sandbox images, exits. |

The API and the worker are **separate containers from the same image**. A
worker that wedges or gets OOM-killed does not take the API down with it, and
you can restart or scale one without the other. Lemma Desktop runs all of this
in a single process instead; that is a packaging choice for a laptop, not a
different architecture.

`migrate` is its own container on purpose. A migration that fails stops the
deploy, instead of leaving the API to crash-loop against a schema it cannot use.

### Three networks

`db`, `redis` and `supertokens` are on `lemma-data`. Agent sandboxes are on
`lemma-sandbox`, which does **not** carry them. Code that an agent writes and
runs therefore cannot open a socket to your database, even though it shares a
Docker daemon with it.

## URLs and ports

```
https://<domain>                 the UI
https://<domain>:8443            the API
https://<slug>.apps.<domain>     pod apps
```

The UI and the API share one hostname on two ports, and this is deliberate.

Ports are not part of cookie host matching, so a **host-only** session cookie
set on `<domain>` is sent to the API on `:8443` — and is never sent to an app
subdomain. Pod apps serve HTML that people in your organization wrote; they must
not receive operator sessions.

The obvious-looking alternative, `api.<domain>` with
`SESSION_COOKIE_DOMAIN=.<domain>`, hands the session cookie to every pod app.
Do not do that. If you need the API on its own hostname, put pod apps on a
separate registrable domain, and change `APP_BASE_DOMAIN` to match.

Open **80**, **443** and **8443** (TCP and UDP — Caddy serves HTTP/3) on the
machine's firewall. Nothing else needs to be reachable.

## Using your own domain

Out of the box `bootstrap.sh` serves on `<your-public-ip>.sslip.io`. sslip.io
resolves any name of that shape back to the IP inside it, including
`anything.apps.<ip>.sslip.io`, so pod apps work with no DNS records at all.
Certificates come from Caddy's own certificate authority, so browsers warn once
and command-line clients need `--insecure`.

That is a way to try Lemma, not a way to run it. For a real deployment:

1. Point an `A` record at the machine for `lemma.example.com`, and a **wildcard**
   `A` record for `*.apps.lemma.example.com`.
2. Make ports 80 and 443 reachable from the internet — Let's Encrypt validates
   over them.
3. Re-run bootstrap:

   ```bash
   ./bootstrap.sh --force --domain lemma.example.com --email you@example.com
   docker compose up -d
   ```

Each pod app subdomain gets its own certificate the first time somebody loads
it. A wildcard certificate would need a DNS-01 challenge, which needs a DNS
provider plugin compiled into Caddy; issuing on demand needs neither.

`--force` rewrites `.env` from the current release and options. The database
password and the encryption keys already in it are kept — rerolling those would
lock the deployment out of its own Postgres and make every stored credential
undecryptable. Anything else you edited by hand is lost.

> **Do not point `--email` at an sslip.io or nip.io name.** Every user of those
> services shares one Let's Encrypt quota for the whole domain, and it is
> routinely exhausted. `bootstrap.sh` refuses.

## What the Docker socket means

`WORKSPACE_PROVIDER=docker` provisions agent sandboxes on this machine's own
Docker daemon, so the `api` and `worker` containers mount `/var/run/docker.sock`
and run as root. Access to that socket is equivalent to root on the host.

That is a real decision, and it is worth being clear about what it does and does
not mean:

- The Lemma backend is what holds the socket, not agent code. Sandboxes are
  containers it creates; they do not get the socket themselves.
- Anyone who can execute code **in the API or worker container** can take the
  host. Treat the machine as dedicated to Lemma.
- Sandboxes are on a network without the database, and their images are pinned
  by digest — a tag-only reference is refused, so the image that runs is the
  image that was published.

If that trade is not one you want to make, set `WORKSPACE_PROVIDER=e2b` and an
`E2B_API_KEY`. Sandboxes are then rented rather than run locally, and neither
container needs the socket. Also set `E2B_METADATA_NAMESPACE` to something
unique to this deployment: two deployments sharing an E2B account and a
namespace will each destroy the other's sandboxes.

## Configuration

`.env` holds what a deployment must decide, and each entry is commented.
[Configuration](configuration.md) documents every operator-facing setting;
anything you add to `.env` reaches the API, worker and migration containers.

The three that most deployments change first:

**Email.** Ships as `EMAIL_TRANSPORT=filesystem`, which writes messages to disk
instead of sending them. Fine for a trial, wrong for anything with a second
person in it — invitations and password resets do not arrive. Set SMTP or Resend
credentials, then turn on `AUTH_EMAIL_VERIFICATION_REQUIRED`. See
[authentication hardening](authentication-hardening.md).

**Models.** `LEMMA_OPENAI_API_KEY` plus `LEMMA_OPENAI_DEFAULT_MODEL`, or the
Anthropic pair. `LEMMA_OPENAI_BASE_URL` points at any compatible endpoint.

**Storage.** Files and objects default to Docker volumes on this machine. For
S3, GCS or Azure see
[object storage](../lemma-backend/docs/operators/object-storage.md).

### Secrets

`SECRET_ENCRYPTION_KEY` encrypts connector credentials, auth-config payloads and
runtime-profile credentials at rest. Lose it and those rows are unreadable; leak
it and they are plaintext. It is generated once, by `bootstrap.sh`, and belongs
in the same backup as the database — not in a different one, and not in neither.

`WORKSPACE_RUNTIME_CREDENTIAL_KEY` signs the token each sandbox uses to call
back. Rotating it invalidates running sandboxes, which are rebuilt.

`.env` is mode 600 and gitignored. Keep it that way.

## Bring your own load balancer

Comment out `COMPOSE_PROFILES=caddy` in `.env` and Caddy does not start. Publish
`api:8000` and `frontend:8080` however your platform expects, and point
`API_URL`, `FRONTEND_URL`, `AUTH_FRONTEND_URL` and `APP_BASE_DOMAIN` at the names
your load balancer serves.

Whatever terminates TLS has to:

- **preserve the `Host` header.** Pod apps are routed by hostname. A proxy that
  rewrites `Host` serves every app as the wrong app, or as none.
- **not buffer responses.** Agent output, run events and every other SSE
  endpoint have to reach the browser as they are produced. In nginx that is
  `proxy_buffering off`; the bundled Caddy config uses `flush_interval -1`.
- **allow long-lived responses.** An agent run outlasts a 60-second read
  timeout.
- **accept large uploads.** Datastore ingestion sends whole documents.

`lemma-backend/nginx.conf` is a worked nginx version of the same contract,
including the header rewriting an ingress may do instead of leaving hostname
routing to the backend.

## Backups

Two things, and they must be taken together — the database rows are encrypted
with the key in `.env`:

```bash
docker compose exec -T db pg_dumpall -U postgres | gzip > lemma-$(date +%F).sql.gz
cp .env lemma-env-$(date +%F).backup
```

Uploaded files and objects live in the `lemma_object_storage` and
`lemma_file_storage` volumes when `STORAGE_BACKEND=local`. Back those up too, or
move object storage to S3/GCS/Azure and let it handle durability.

Restore into an empty stack. `.env` goes back first — Compose needs
`LEMMA_POSTGRES_IMAGE` and `POSTGRES_PASSWORD` before it can start anything, and
the dump is encrypted against the key in it:

```bash
cp lemma-env-<date>.backup .env
docker compose up -d db
gunzip -c lemma-<date>.sql.gz | docker compose exec -T db psql -U postgres -d postgres
docker compose up -d
```

`pg_dumpall` output carries its own `\connect` lines, so this restores all three
databases. Nothing needs `psql` on the host — it runs inside the container that
already has it.

## Upgrades

```bash
./bootstrap.sh --force --version 0.8.0   # writes new image digests into .env
docker compose up -d
```

`migrate` runs before the API starts and applies schema changes. If it fails the
deploy stops there and the previous containers keep serving, which is the point
of it being a separate container.

`--force` keeps the secrets and rewrites everything else, so any hand-edited
settings go with it. To upgrade while keeping those, edit the `LEMMA_*_IMAGE`,
`WORKSPACE_IMAGE` and `FUNCTION_IMAGE` lines by hand from the release manifest:

```bash
curl -fsSL https://github.com/lemma-work/lemma-platform/releases/latest/download/lemma-local.json
```

Read the release notes before a major version. Postgres will not start on a data
directory written by an older major, so a Postgres bump needs a dump and reload.

## When something is wrong

```bash
docker compose ps                      # what is up, what is healthy
docker compose logs -f api worker      # the two that do the work
docker compose logs migrate            # a failed deploy is usually here
docker compose exec db psql -U postgres -l
curl -k https://<domain>:8443/health/ready
```

`/health/ready` names the dependency that is failing rather than just returning
503, which is usually enough on its own.

Common ones:

- **`migrate` exits non-zero on first start.** Almost always the database
  volume outliving a Postgres major version bump. `docker compose down -v`
  destroys it and starts clean — only ever on a deployment with nothing in it.
- **The UI loads but sign-in fails.** `API_URL` in `.env` does not match the
  URL the browser actually reached. They have to agree exactly, port included.
- **A pod app 404s.** `APP_BASE_DOMAIN` must be `apps.<domain>`, and
  `*.apps.<domain>` must resolve here.
- **Agent runs fail immediately.** No model key, or `sandbox-images` did not
  finish — `docker compose logs sandbox-images`.
- **`api` sits at "Waiting for application startup." forever.** With no model
  provider key, `EMBEDDING_PROVIDER=auto` falls back to a local model, and the
  api and worker each download ~210 MB from HuggingFace before they serve. The
  download is unauthenticated and has no overall timeout, so on a slow or
  filtered link it can stall with nothing in the log after the "unauthenticated
  requests to the HF Hub" warning.

  `.env` ships `HF_HUB_DISABLE_XET=1`, which keeps that transfer on
  HuggingFace's plain CDN rather than its Xet storage — measured here, Xet hung
  silently for over an hour while the CDN path reported a timeout, resumed and
  finished. If it still stalls: set `HF_TOKEN` for a higher rate limit, or
  better, configure a model provider key, because `auto` then uses that
  endpoint for embeddings and downloads nothing at all.

  The model is cached in a per-service volume, so it is a first-start cost, not
  a restart one.

## What this does not do

- **One machine.** No horizontal scaling, no managed Postgres, no HA. Those are
  real deployments and this is a compose file; the settings are all there if you
  want to point `DATABASE_URL` at something managed.
- **No automatic upgrades.** Upgrading is the two commands above, when you
  choose.
- **No built-in monitoring.** [Observability](observability.md) covers exporting
  traces, metrics and logs to any OTLP collector.
