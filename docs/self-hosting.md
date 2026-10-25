# Self-host Lemma on a server

Run Lemma for your team on one machine you control: a VPS, a cloud VM, or a box
in the office. Everything is one Docker Compose file in
[`deploy/compose/`](../deploy/compose).

Just want Lemma on your own laptop? Use [Lemma Desktop](installation.md) instead.

## What you need

- A Linux machine with **4 vCPU, 8 GB of RAM and 40 GB of disk**.
- **Docker Engine 25+** with the Compose v2 plugin.
- `curl` and `python3`, which most server images already have.
- An **API key for a model provider** (OpenAI, Anthropic, or anything compatible
  with either). Agents don't work without one.
- A domain, optionally. Without one, Lemma still runs over HTTPS but browsers
  show a certificate warning.

## Install

```bash
git clone https://github.com/lemma-work/lemma-platform.git
cd lemma-platform/deploy/compose
./bootstrap.sh
```

`bootstrap.sh` creates a `.env` file with generated secrets and the latest
release's images. Open `.env`, add your model provider key, then start it:

```bash
docker compose up -d
```

The first start downloads about 1.5 GB and takes a few minutes. When
`docker compose ps` shows `api` as healthy, open the URL `bootstrap.sh` printed
and create the first account.

Open ports **80**, **443** and **8443** (TCP and UDP) on the firewall. Nothing
else needs to be reachable.

## Use your own domain

Out of the box Lemma serves on `<your-ip>.sslip.io` with a self-signed
certificate. That's fine for trying it; for real use, give it a domain:

1. Point an `A` record for `lemma.example.com` at the machine, plus a wildcard
   `A` record for `*.apps.lemma.example.com` (pod apps live there).
2. Make sure ports 80 and 443 are reachable from the internet.
3. Re-run bootstrap, put back what you set by hand, and restart:

   ```bash
   cp .env .env.before-bootstrap
   ./bootstrap.sh --force --domain lemma.example.com --email you@example.com
   ```

   `--force` keeps your database password and encryption keys, but rewrites
   everything else in `.env`. Copy your model, email and any other settings you
   added back from `.env.before-bootstrap`, then start it:

   ```bash
   docker compose up -d
   ```

Certificates come from Let's Encrypt.

Lemma then lives at:

```
https://lemma.example.com                 the app
https://lemma.example.com:8443            the API
https://<app>.apps.lemma.example.com      pod apps
```

## Configure

Every setting in `.env` is commented, and [configuration](configuration.md)
lists them all. The ones to set first:

- **Email.** Out of the box, email is written to disk instead of sent, so
  invitations and password resets never arrive. Add SMTP or Resend credentials,
  then set `AUTH_EMAIL_VERIFICATION_REQUIRED=true`. See
  [authentication hardening](authentication-hardening.md).
- **Models.** `LEMMA_OPENAI_API_KEY` and `LEMMA_OPENAI_DEFAULT_MODEL`, or the
  Anthropic pair. Set `LEMMA_OPENAI_BASE_URL` for any compatible endpoint.
- **Storage.** Files are stored in Docker volumes on the machine. To use S3, GCS
  or Azure, see [object storage](../lemma-backend/docs/operators/object-storage.md).

`.env` holds `SECRET_ENCRYPTION_KEY`, which encrypts stored credentials. If you
lose it, those credentials can't be read, so back it up with the database.

## What the Docker socket means

Agents run code in sandbox containers on the same machine, so the `api` and
`worker` containers have access to the Docker socket. That access is equivalent
to root on the host, so dedicate the machine to Lemma. The sandboxes themselves
don't get the socket, and they run on a separate network that cannot reach the
database.

If you'd rather not, set `WORKSPACE_PROVIDER=e2b` with an `E2B_API_KEY` to run
sandboxes on [E2B](https://e2b.dev) instead. Give each deployment its own
`E2B_METADATA_NAMESPACE`, or two deployments sharing an account will delete each
other's sandboxes.

## Use your own load balancer

Comment out `COMPOSE_PROFILES=caddy` in `.env` to skip the bundled Caddy. Expose
`api:8000` and `frontend:8080`, and set `API_URL`, `FRONTEND_URL`,
`AUTH_FRONTEND_URL` and `APP_BASE_DOMAIN` to your hostnames. Your proxy must:

- keep the `Host` header, since pod apps are routed by hostname;
- not buffer responses, so streamed agent output arrives live (in nginx,
  `proxy_buffering off`);
- allow responses longer than 60 seconds;
- accept large uploads.

Leave `SESSION_COOKIE_DOMAIN` empty, even with the API and the app on separate
hostnames: the session cookie then stays on the API's own host. Setting it to
your parent domain (`.example.com`) sends it to every pod app as well, and pod
apps are pages people in your organization write. See
[URLs, CORS and cookies](configuration.md#urls-cors-and-cookies).

## Back up and restore

Back up the database, `.env` and the two volumes that hold uploaded files
together. The commands below are for the bundled `db` service and files kept in
Docker volumes, the default:

- With a managed Postgres (`DATABASE_URL` pointing elsewhere), use your
  provider's snapshots, or dump both databases Lemma keeps with a client in a
  container (its major version at least your server's) in place of the
  `pg_dumpall` line:

  ```bash
  for url in DATABASE_URL DATASTORE_DATABASE_URL; do
    docker run --rm --env-file .env postgres:18 \
      bash -c 'u=${!1}; pg_dump "${u/+asyncpg/}"' _ "$url" | gzip > lemma-$url-$(date +%F).sql.gz
  done
  ```

  and restore each the same way, with `-i`, `gunzip -c` piped in and `psql` for
  `pg_dump`, in place of the `psql` line. The `+asyncpg` is stripped because
  the client takes a plain `postgresql://` URL; if yours carries `ssl=`, give
  it the client's `sslmode=` instead.
- With S3, GCS or Azure storage, the files are in your bucket: keep it with
  your provider's versioning or replication, and skip both volume loops.

```bash
docker compose exec -T db pg_dumpall -U postgres | gzip > lemma-$(date +%F).sql.gz
cp .env lemma-env-$(date +%F).backup
for volume in object_storage file_storage; do
  docker run --rm -v lemma_$volume:/data -v "$PWD":/backup alpine \
    tar czf /backup/lemma-$volume-$(date +%F).tgz -C /data .
done
```

To restore onto a fresh install:

```bash
cp lemma-env-<date>.backup .env
docker compose create   # makes the volumes; starts nothing
for volume in object_storage file_storage; do
  docker run --rm -v lemma_$volume:/data -v "$PWD":/backup alpine \
    tar xzf /backup/lemma-$volume-<date>.tgz -C /data
done
docker compose up -d --wait db
gunzip -c lemma-<date>.sql.gz | docker compose exec -T db psql -U postgres -d postgres
docker compose up -d
```

## Upgrade

```bash
cp .env .env.before-bootstrap
./bootstrap.sh --force --version 0.9.0
```

As with a new domain, copy the settings you added by hand back from
`.env.before-bootstrap` (and pass the same `--domain` and `--email` again if you
use your own), then:

```bash
docker compose up -d
```

Database migrations run before the new version starts. If one fails, the old
version keeps running. Read the release notes before a major version: a Postgres
major upgrade needs a dump and restore.

## Troubleshooting

```bash
docker compose ps                     # what is running and healthy
docker compose logs -f api worker     # the main services
docker compose logs migrate           # failed upgrades show up here
curl -k https://<domain>:8443/health/ready   # names whatever is failing
```

| Problem | Fix |
| --- | --- |
| The app loads but sign-in fails | `API_URL` in `.env` must exactly match the URL in the browser, port included |
| A pod app returns 404 | `APP_BASE_DOMAIN` must be `apps.<domain>`, and `*.apps.<domain>` must resolve to this machine |
| Agent runs fail straight away | Check the model key, then `docker compose logs sandbox-images` |
| `api` is stuck on "Waiting for application startup" | Without a model key it downloads a 210 MB embedding model first. Add a model key, or set `HF_TOKEN` if the download stalls |
| `migrate` fails on first start | An old database volume from another Postgres version. On an empty install only, `docker compose down -v` starts clean |
| Every table query, agent SQL call and querying workflow answers `DATASTORE_QUERY_UNAVAILABLE` | The datastore's database role cannot create roles. `/health/ready` shows `"datastore_roles": "missing_createrole"`. Grant it: `ALTER ROLE <role> CREATEROLE` |

## Limits

This is a single-machine setup: no high availability, no automatic upgrades,
and no built-in monitoring. To use a managed Postgres, point `DATABASE_URL` at
it. The role in `DATASTORE_DATABASE_URL` (or `DATABASE_URL`, when that is unset)
needs `CREATEROLE`: each pod's queries run as a role of its own, made when the
pod's schema is. For metrics and traces, see [observability](observability.md).
