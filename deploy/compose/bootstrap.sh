#!/usr/bin/env bash
# First-run setup for a self-hosted Lemma.
#
# Writes .env next to this file: the hostname to serve on, freshly generated
# secrets, and image references pinned by digest from a published release. Run
# it once, then `docker compose up -d`.
#
# It will not overwrite an existing .env. Delete it deliberately, or pass
# --force, if you mean to start over — the encryption key in it is the only
# thing that can read this deployment's stored credentials.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
env_file="$here/.env"
template="$here/.env.example"

repo="${LEMMA_RELEASE_REPO:-lemma-work/lemma-platform}"
channel="stable"
manifest_file=""
domain=""
email=""
force=0

usage() {
	cat <<'USAGE'
Usage: ./bootstrap.sh [options]

  --domain <name>     Hostname to serve on. Defaults to <public-ip>.sslip.io,
                      which resolves to this machine with no DNS setup.
  --email <address>   Get publicly trusted Let's Encrypt certificates for
                      --domain instead of using Caddy's own CA. Requires a real
                      domain with an A record for it and for *.apps.<domain>,
                      and ports 80 and 443 reachable from the internet.
  --version <X.Y.Z>   Release to install. Defaults to the latest.
  --manifest <path>   Install from a local release manifest instead.
  --force             Overwrite an existing .env.
USAGE
}

while [ $# -gt 0 ]; do
	case "$1" in
	--domain) domain="${2:?--domain needs a value}"; shift 2 ;;
	--email) email="${2:?--email needs a value}"; shift 2 ;;
	--version) channel="${2:?--version needs a value}"; shift 2 ;;
	--manifest) manifest_file="${2:?--manifest needs a value}"; shift 2 ;;
	--force) force=1; shift ;;
	-h | --help) usage; exit 0 ;;
	*) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
	esac
done

die() { echo "error: $*" >&2; exit 1; }

command -v curl >/dev/null 2>&1 || die "curl is not installed"
command -v docker >/dev/null 2>&1 || die "docker is not installed"
docker compose version >/dev/null 2>&1 || die "docker compose v2 is not available"
command -v python3 >/dev/null 2>&1 ||
	die "python3 is required to read the release manifest (apt-get install -y python3)"
[ -r "$template" ] || die "missing $template"

if [ -e "$env_file" ] && [ "$force" -ne 1 ]; then
	die "$env_file already exists. Pass --force to rewrite it from the current
release and options; the database password and encryption keys in it are kept
as they are. Anything else you have edited by hand will be lost."
fi

# ── where to serve ────────────────────────────────────────────────────────────
if [ -z "$domain" ]; then
	echo "→ resolving this machine's public address…"
	ip=""
	for resolver in https://api.ipify.org https://ifconfig.me/ip https://icanhazip.com; do
		ip="$(curl -fsS --max-time 10 "$resolver" 2>/dev/null | tr -d '[:space:]')" || ip=""
		case "$ip" in
		[0-9]*.[0-9]*.[0-9]*.[0-9]*) break ;;
		*) ip="" ;;
		esac
	done
	[ -n "$ip" ] || die "could not determine this machine's public IP. Pass --domain."
	# sslip.io answers <ip>.sslip.io — and anything.<ip>.sslip.io — with that IP,
	# so pod app subdomains work without owning a domain. Certificates for these
	# names come from Caddy's own CA: every sslip.io name on the internet shares
	# one Let's Encrypt quota and it is routinely exhausted.
	domain="${ip}.sslip.io"
	echo "  serving on $domain"
fi

if [ -n "$email" ]; then
	case "$domain" in
	*.sslip.io | *.nip.io)
		die "$domain shares one Let's Encrypt quota with every other user of
that service, and it is routinely exhausted. Use a domain you control, or drop
--email and accept Caddy's own certificate authority."
		;;
	esac
	tls_arg="$email"
	apps_tls_opts="on_demand"
else
	tls_arg="internal"
	apps_tls_opts=""
fi

api_port="${LEMMA_API_PORT:-8443}"

# ── the release to install ────────────────────────────────────────────────────
manifest_json=""
if [ -n "$manifest_file" ]; then
	[ -r "$manifest_file" ] || die "cannot read $manifest_file"
	manifest_json="$(cat "$manifest_file")"
	echo "→ using release manifest $manifest_file"
else
	if [ "$channel" = "stable" ]; then
		url="https://github.com/$repo/releases/latest/download/lemma-local.json"
	else
		url="https://github.com/$repo/releases/download/v${channel#v}/lemma-local.json"
	fi
	echo "→ fetching release manifest…"
	manifest_json="$(curl -fsSL --max-time 60 "$url")" ||
		die "could not fetch $url. Pass --version <X.Y.Z> for a specific release,
or --manifest <path> to install from a file."
fi

# The same manifest lemma-stack and Lemma Desktop install from, so a compose
# deployment of a version runs the identical images. Digests matter: the
# workspace provider rejects a sandbox image that is not pinned by @sha256.
images="$(printf '%s' "$manifest_json" | python3 -c '
import json
import shlex
import sys

DEFAULT_INFRA = {
    "postgres": "docker.io/pgvector/pgvector:0.8.3-pg18",
    "redis": "docker.io/redis:7.4-alpine",
    "supertokens": "docker.io/supertokens/supertokens-postgresql:11.4.5",
}

manifest = json.load(sys.stdin)


def ref(entry):
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict) and entry.get("ref"):
        digest = entry.get("digest")
        base = entry["ref"]
        return base + "@" + digest if digest else base
    raise SystemExit("release manifest has a malformed image entry")


app = manifest.get("images") or {}
missing = [k for k in ("backend", "frontend", "workspace", "function") if k not in app]
if missing:
    raise SystemExit("release manifest is missing images: " + ", ".join(missing))

infra = manifest.get("infra") or {}
out = {
    "LEMMA_VERSION": manifest.get("version", "unknown"),
    "LEMMA_SOURCE_SHA": manifest.get("source_sha", ""),
    "LEMMA_BACKEND_IMAGE": ref(app["backend"]),
    "LEMMA_FRONTEND_IMAGE": ref(app["frontend"]),
    "WORKSPACE_IMAGE": ref(app["workspace"]),
    "FUNCTION_IMAGE": ref(app["function"]),
}
for key, fallback in DEFAULT_INFRA.items():
    out["LEMMA_" + key.upper() + "_IMAGE"] = ref(infra[key]) if key in infra else fallback

for name in ("WORKSPACE_IMAGE", "FUNCTION_IMAGE"):
    if "@sha256:" not in out[name]:
        raise SystemExit(
            f"{name} is not pinned by digest ({out[name]}). The workspace "
            "provider refuses a sandbox image pinned only by tag."
        )

# Quoted because the caller evals this. The values come from a manifest fetched
# over the network, and "we publish it" is not the same as "it cannot contain a
# space".
for key, value in out.items():
    print(f"{key}={shlex.quote(value)}")
')" || die "could not read the release manifest"

eval "$(printf '%s\n' "$images" | sed 's/^/export /')"
echo "  release ${LEMMA_VERSION}"

# Caddy and the Docker CLI are upstream images this deployment picks rather than
# ones Lemma publishes, so they are not in the release manifest and arrive here
# as moving tags. Resolve them to digests now and pin those.
#
# Not cosmetic consistency: `sandbox-images` runs the Docker CLI image with the
# host Docker socket mounted, so an attacker who can move that tag can drive the
# daemon and own the machine. A digest cannot be moved.
pin_to_digest() {
	reference="$1"
	case "$reference" in
	*@sha256:*)
		printf '%s' "$reference"
		return 0
		;;
	esac
	# The registry is asked for the digest of the multi-arch index, so one .env
	# is correct on both amd64 and arm64.
	digest="$(docker buildx imagetools inspect --format '{{.Manifest.Digest}}' "$reference" 2>/dev/null || true)"
	if [ -z "$digest" ]; then
		# No buildx: pull it and read the digest the daemon recorded.
		docker pull --quiet "$reference" >/dev/null 2>&1 || {
			printf '%s' "$reference"
			return 1
		}
		printf '%s' "$(docker image inspect --format '{{index .RepoDigests 0}}' "$reference" 2>/dev/null || printf '%s' "$reference")"
		return 0
	fi
	printf '%s@%s' "${reference%%@*}" "$digest"
}

echo "→ pinning the proxy and Docker CLI images…"
caddy_image="$(pin_to_digest "${LEMMA_CADDY_IMAGE:-caddy:2-alpine}")" ||
	echo "  warning: could not resolve a digest for Caddy; leaving the tag"
docker_cli_image="$(pin_to_digest "${LEMMA_DOCKER_CLI_IMAGE:-docker:29-cli}")" ||
	echo "  warning: could not resolve a digest for the Docker CLI; leaving the tag"
case "$docker_cli_image" in
*@sha256:*) ;;
*)
	die "could not resolve a digest for $docker_cli_image, and this one is not
optional: the sandbox-images service runs it with the host Docker socket
mounted. Check the machine can reach the registry, or set
LEMMA_DOCKER_CLI_IMAGE to a digest-pinned reference yourself."
	;;
esac

# `production` is refused at startup without a valid release identity, and
# rightly: a deployment that cannot say which commit it is running cannot be
# debugged from its own logs. Releases published before the manifest carried the
# commit have none to give, so those install as `development` — same behaviour
# in every other respect, minus the identity on each log line.
release_sha="${LEMMA_SOURCE_SHA:-}"
case "$release_sha" in
*[!0-9a-f]* | "") release_sha="" ;;
*) [ "${#release_sha}" -eq 40 ] || release_sha="" ;;
esac
if [ -n "$release_sha" ]; then
	environment="production"
else
	environment="development"
	echo "  note: release ${LEMMA_VERSION} publishes no source commit, so this"
	echo "        installs as ENVIRONMENT=development. Upgrade to a release that"
	echo "        does, or set LEMMA_RELEASE_SHA and ENVIRONMENT=production in .env."
fi

# ── secrets ───────────────────────────────────────────────────────────────────
# Generated once, then carried across every later run.
#
# --force exists to change the domain or move to a new release, and rerolling
# these while doing it would be a quiet catastrophe: Postgres keeps the password
# it was initialised with, so a new one locks the deployment out of its own
# database, and a new SECRET_ENCRYPTION_KEY makes every stored connector
# credential undecryptable. Both were true of this script until a --force
# against a live stack proved it.
#
# To actually rotate one, change it in .env deliberately — and for the
# encryption key, run lemma-backend/scripts/reencrypt_secrets.py first.
gen_key() { python3 -c 'import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())'; }
gen_password() { python3 -c 'import secrets, string; print("".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(40)))'; }

carried_over() {
	[ -r "$env_file" ] || return 0
	sed -n "s/^$1=//p" "$env_file" | head -1
}

postgres_password="$(carried_over POSTGRES_PASSWORD)"
secret_encryption_key="$(carried_over SECRET_ENCRYPTION_KEY)"
runtime_credential_key="$(carried_over WORKSPACE_RUNTIME_CREDENTIAL_KEY)"
if [ -n "$postgres_password$secret_encryption_key$runtime_credential_key" ]; then
	echo "  keeping the database password and encryption keys already in .env"
fi
[ -n "$postgres_password" ] || postgres_password="$(gen_password)"
[ -n "$secret_encryption_key" ] || secret_encryption_key="$(gen_key)"
[ -n "$runtime_credential_key" ] || runtime_credential_key="$(gen_key)"

# ── write it ──────────────────────────────────────────────────────────────────
site_url="https://$domain"
api_url="https://$domain:$api_port"

umask 077
export BOOTSTRAP_DOMAIN="$domain"
export BOOTSTRAP_API_PORT="$api_port"
export BOOTSTRAP_TLS_ARG="$tls_arg"
export BOOTSTRAP_APPS_TLS_OPTS="$apps_tls_opts"
export BOOTSTRAP_POSTGRES_PASSWORD="$postgres_password"
export BOOTSTRAP_DATABASE_URL="postgresql+asyncpg://postgres:$postgres_password@db:5432/lemma"
export BOOTSTRAP_DATASTORE_DATABASE_URL="postgresql+asyncpg://postgres:$postgres_password@db:5432/lemma_datastore"
export BOOTSTRAP_API_URL="$api_url"
export BOOTSTRAP_SITE_URL="$site_url"
export BOOTSTRAP_SECRET_ENCRYPTION_KEY="$secret_encryption_key"
export BOOTSTRAP_RUNTIME_CREDENTIAL_KEY="$runtime_credential_key"
export BOOTSTRAP_ENVIRONMENT="$environment"
export BOOTSTRAP_RELEASE_SHA="$release_sha"
export BOOTSTRAP_CADDY_IMAGE="$caddy_image"
export BOOTSTRAP_DOCKER_CLI_IMAGE="$docker_cli_image"

python3 - "$template" "$env_file" <<'PY'
import os
import sys

template, destination = sys.argv[1], sys.argv[2]

values = {
    "LEMMA_DOMAIN": os.environ["BOOTSTRAP_DOMAIN"],
    "LEMMA_API_PORT": os.environ["BOOTSTRAP_API_PORT"],
    "LEMMA_TLS_ARG": os.environ["BOOTSTRAP_TLS_ARG"],
    "LEMMA_APPS_TLS_OPTS": os.environ["BOOTSTRAP_APPS_TLS_OPTS"],
    "LEMMA_BACKEND_IMAGE": os.environ["LEMMA_BACKEND_IMAGE"],
    "LEMMA_FRONTEND_IMAGE": os.environ["LEMMA_FRONTEND_IMAGE"],
    "WORKSPACE_IMAGE": os.environ["WORKSPACE_IMAGE"],
    "FUNCTION_IMAGE": os.environ["FUNCTION_IMAGE"],
    "LEMMA_POSTGRES_IMAGE": os.environ["LEMMA_POSTGRES_IMAGE"],
    "LEMMA_REDIS_IMAGE": os.environ["LEMMA_REDIS_IMAGE"],
    "LEMMA_SUPERTOKENS_IMAGE": os.environ["LEMMA_SUPERTOKENS_IMAGE"],
    "POSTGRES_PASSWORD": os.environ["BOOTSTRAP_POSTGRES_PASSWORD"],
    "DATABASE_URL": os.environ["BOOTSTRAP_DATABASE_URL"],
    "DATASTORE_DATABASE_URL": os.environ["BOOTSTRAP_DATASTORE_DATABASE_URL"],
    "API_URL": os.environ["BOOTSTRAP_API_URL"],
    "FRONTEND_URL": os.environ["BOOTSTRAP_SITE_URL"],
    "AUTH_FRONTEND_URL": os.environ["BOOTSTRAP_SITE_URL"] + "/auth",
    "CLI_API_URL": os.environ["BOOTSTRAP_API_URL"],
    "CLI_AUTH_FRONTEND_URL": os.environ["BOOTSTRAP_SITE_URL"] + "/auth",
    "APP_BASE_DOMAIN": "apps." + os.environ["BOOTSTRAP_DOMAIN"],
    "SECRET_ENCRYPTION_KEY": os.environ["BOOTSTRAP_SECRET_ENCRYPTION_KEY"],
    "WORKSPACE_RUNTIME_CREDENTIAL_KEY": os.environ["BOOTSTRAP_RUNTIME_CREDENTIAL_KEY"],
    "ENVIRONMENT": os.environ["BOOTSTRAP_ENVIRONMENT"],
    "LEMMA_RELEASE_SHA": os.environ["BOOTSTRAP_RELEASE_SHA"],
    "LEMMA_CADDY_IMAGE": os.environ["BOOTSTRAP_CADDY_IMAGE"],
    "LEMMA_DOCKER_CLI_IMAGE": os.environ["BOOTSTRAP_DOCKER_CLI_IMAGE"],
}

lines = []
unset = []
for line in open(template, encoding="utf-8").read().splitlines():
    key = line.split("=", 1)[0] if "=" in line and not line.startswith("#") else None
    if key in values:
        value = values[key]
        # An empty assignment in an env_file is not "unset" — it overrides the
        # image's own value with the empty string. LEMMA_RELEASE_SHA is baked
        # into the backend image at build time, so writing a blank line here
        # would erase the identity the image already carries.
        line = f"{key}={value}" if value else f"# {key}="
    elif line.endswith("=GENERATED"):
        unset.append(key)
    lines.append(line)

if unset:
    raise SystemExit("bootstrap has no value for: " + ", ".join(unset))

header = [
    "# Generated by bootstrap.sh. Edit freely — it is never rewritten.",
    f"# Release {os.environ['LEMMA_VERSION']}.",
    "",
]
open(destination, "w", encoding="utf-8").write("\n".join(header + lines) + "\n")
PY

chmod 600 "$env_file"

cat <<EOF

✓ Wrote $env_file

  Next:
    1. Set a model provider key in .env — agents need one.
       LEMMA_OPENAI_API_KEY and LEMMA_OPENAI_DEFAULT_MODEL, or the Anthropic pair.
    2. docker compose -f "$here/docker-compose.yml" up -d

  Then open $site_url
  The API is at $api_url
EOF

if [ "$tls_arg" = "internal" ]; then
	cat <<'EOF'

  Certificates come from Caddy's own authority, so your browser will warn once
  and every command-line client needs --insecure. That is the cost of not
  owning a domain. When you have one, re-run:

    ./bootstrap.sh --force --domain lemma.example.com --email you@example.com
EOF
fi
