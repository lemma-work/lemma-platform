# Gogett deployment notes

This fork (`0xtsotsi/gogett-hub`) runs the upstream `lemma-work/lemma-platform`
stack under the **gogett** brand, exposed at `https://gogett.webrnds.com` via
a Cloudflare Tunnel from a single Mac.

## What's different from upstream

1. **Brand layer only.** UI copy, badges, and the marketing site mention
   `gogett.webrnds.com` instead of `lemma.work`. The `lemma-stack` Python
   CLI, `lemma-*` Python packages, install URL, and upstream CDN refs are
   untouched — so `git fetch upstream` stays clean and mergeable.
2. **Custom frontend image.** Upstream's `lemma-frontend` ships without the
   Next.js `rewrites()` that proxy `/st/auth/*` (SuperTokens SDK),
   `/api/v1/*`, `/users/*`, `/agent-runtime/*`, `/organizations/*`, and
   `/pods/*` to the backend service after real Next.js pages have a chance
   to match. We rebuild from source with the patched
   `lemma-frontend/next.config.ts`; fallback rewrites keep app routes such
   as `/organizations/*/settings/*` in Next.js while still letting API calls
   such as `/users/me` reach FastAPI. A `beforeFiles` rewrite for
   `/auth/cli/:path*` is required so the CLI's `auth login` flow can reach
   the backend device-link endpoints without the auth UI's catch-all
   claiming the request (Next.js evaluates `beforeFiles` rewrites before
   physical routes; `fallback` runs after).
3. **Custom env overrides** for `lemma-stack**:
   - `CORS_ORIGIN_REGEX` allows `gogett.webrnds.com` (default is locked to
     `127-0-0-1.sslip.io`).
   - `FRONTEND_URL` / `AUTH_FRONTEND_URL` / `API_URL` → `https://gogett.webrnds.com`.
   - `SESSION_COOKIE_DOMAIN` → `.gogett.webrnds.com`, `SESSION_COOKIE_SECURE` → true.
   - `NEXT_PUBLIC_*` (frontend env section) — same public host, gateway path
     `/st`, base path `/auth`, session cookie domain `.gogett.webrnds.com`.
4. **Model provider.** `LEMMA_DEFAULT_MODEL_TYPE=openai_compat` with
   MiniMax base URL + key. Change to `anthropic_compat` if you swap providers.

## Components

| Component | Where |
|---|---|
| Public URL | `https://gogett.webrnds.com` |
| Cloudflare Tunnel | `~/.cloudflared/{cert.pem, config.yml, 47dd90f6-….json}` |
| Custom frontend image | `lemma-frontend-gogett:local` (built locally) |
| Docker network | `lemma-local-net` (auto-created by `lemma-stack install`) |
| App (frontend) port | `3711` (container) → `8080` (Next.js) |
| API port | `8711` (host) → `8000` (FastAPI inside container) |
| Lemma stack config | `~/.lemma/local/config.toml` |
| Stack installer | `uv tool install --from ./gogett-hub/lemma-stack lemma-stack` |
| Frontend rebuild script | `~/bin/rebuild-frontend.sh` |
| Token refresh helper | `~/bin/gogett-token.sh` |
| Cloudflare daemon | `~/Library/LaunchAgents/com.gogetta.cloudflared-gogett.plist` |

## Reproducing on a fresh machine

### 1. Prereqs

```bash
brew install uv cloudflared docker
# Start Docker Desktop and wait for the daemon.
```

### 2. Clone the fork

```bash
git clone https://github.com/0xtsotsi/gogett-hub.git ~/Documents/projects/gogett-hub
cd ~/Documents/projects/gogett-hub
```

### 3. Install `lemma-stack` (from the fork's source, not PyPI)

```bash
uv tool install --from ./gogett-hub/lemma-stack lemma-stack
# Verify:
PATH="$(uv tool dir)/bin:$PATH" lemma-stack --help
```

### 4. Authorize Cloudflare

```bash
cloudflared tunnel login
# Browser opens → pick the `webrnds.com` zone → Authorize.
# cert.pem lands at ~/.cloudflared/cert.pem.
```

### 5. Create the tunnel + DNS

```bash
TUNNEL_UUID=$(cloudflared tunnel create gogett | awk '/Created tunnel/ {print $NF}')
# CNAME gogett.webrnds.com → tunnel:
cloudflared tunnel route dns --overwrite-dns gogett gogett.webrnds.com
```

### 6. Write `~/.cloudflared/config.yml`

```yaml
tunnel: $TUNNEL_UUID          # paste the UUID from step 5
credentials-file: /Users/$USER/.cloudflared/$TUNNEL_UUID.json

ingress:
  - hostname: gogett.webrnds.com
    service: http://127-0-0-1.sslip.io:3711
    originRequest:
      httpHostHeader: 127-0-0-1.sslip.io
      noTLSVerify: false
  - service: http_status:404
```

### 7. Install the tunnel daemon

```bash
cat > ~/Library/LaunchAgents/com.gogetta.cloudflared-gogett.plist <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.gogetta.cloudflared-gogett</string>
  <key>ProgramArguments</key>
  <array>
    <string>/opt/homebrew/bin/cloudflared</string>
    <string>tunnel</string>
    <string>--config</string>
    <string>/Users/$(whoami)/.cloudflared/config.yml</string>
    <string>run</string>
    <string>gogett</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key><false/>
    <key>Crashed</key><true/>
  </dict>
  <key>StandardOutPath</key><string>/Users/$(whoami)/.cloudflared/gogett-tunnel.log</string>
  <key>StandardErrorPath</key><string>/Users/$(whoami)/.cloudflared/gogett-tunnel.err</string>
</dict>
</plist>
PLIST

launchctl load ~/Library/LaunchAgents/com.gogetta.cloudflared-gogett.plist
launchctl list | grep gogett   # expect a row with com.gogetta.cloudflared-gogett
```

### 8. Install + start the Lemma stack

```bash
PATH="$(uv tool dir)/bin:$PATH" lemma-stack install --runtime auto -y
```

### 9. Override stack env for the public hostname

```bash
PATH="$(uv tool dir)/bin:$PATH"
lemma-stack config set CORS_ORIGIN_REGEX '^https?://(([a-z0-9-]+\.)?127-0-0-1\.sslip\.io|gogett\.webrnds\.com)(:\d+)?$'
lemma-stack config set FRONTEND_URL 'https://gogett.webrnds.com'
lemma-stack config set AUTH_FRONTEND_URL 'https://gogett.webrnds.com/auth'
lemma-stack config set API_URL 'https://gogett.webrnds.com'
lemma-stack config set SESSION_COOKIE_DOMAIN '.gogett.webrnds.com'
lemma-stack config set SESSION_COOKIE_SECURE 'true'
lemma-stack config set SESSION_COOKIE_SAME_SITE 'lax'

# Model provider (MiniMax example — swap to your own):
lemma-stack config set LEMMA_DEFAULT_MODEL_TYPE openai_compat
lemma-stack config set LEMMA_OPENAI_BASE_URL 'https://api.minimax.io/v1'
lemma-stack config set LEMMA_OPENAI_API_KEY '<your-key>'
lemma-stack config set LEMMA_OPENAI_DEFAULT_MODEL 'MiniMax-M3'
lemma-stack config set LEMMA_OPENAI_MODEL_NAMES 'MiniMax-M3,MiniMax-M2.7,MiniMax-M2.7-highspeed'

# At-rest Fernet key for connector creds + webhook secrets — set BEFORE the
# stack first writes any account/connector. The default local seed is public
# and now requires LEMMA_ALLOW_LOCAL_FALLBACK_KEY=true to enable; in any
# non-ephemeral deployment you must generate a real key, then restart, then
# re-encrypt existing rows forward (e.g. via the scripts/reencrypt_secrets.py
# helper) if you had any data written under the old seed.
lemma-stack config set SECRET_ENCRYPTION_KEY "$(uv tool run --from cryptography python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"

# Frontend NEXT_PUBLIC_* overrides — bare UPPER_SNAKE routes to backend.env
# in lemma-stack, so use the dotted form:
lemma-stack config set frontend.env.NEXT_PUBLIC_SITE_URL 'https://gogett.webrnds.com'
lemma-stack config set frontend.env.NEXT_PUBLIC_AUTH_URL 'https://gogett.webrnds.com/auth'
lemma-stack config set frontend.env.NEXT_PUBLIC_API_URL 'https://gogett.webrnds.com'
lemma-stack config set frontend.env.NEXT_PUBLIC_AUTH_DEFAULT_REDIRECT_URI 'https://gogett.webrnds.com/'
lemma-stack config set frontend.env.NEXT_PUBLIC_SUPERTOKENS_API_BASE_PATH '/auth'
lemma-stack config set frontend.env.NEXT_PUBLIC_SUPERTOKENS_API_GATEWAY_PATH '/st'
lemma-stack config set frontend.env.NEXT_PUBLIC_SESSION_TOKEN_DOMAIN '.gogett.webrnds.com'
```

### 10. Build the custom frontend image

```bash
# This is the script shipped in ~/bin — rebuilds + restarts the frontend container.
~/bin/rebuild-frontend.sh
```

The script is idempotent: it stops any existing `lemma-local-frontend`
container, rebuilds `lemma-frontend-gogett:local` from
`lemma-frontend/Dockerfile`, and starts a fresh container with the public
host env baked in.

### 11. Create your first account + org + pod

```bash
# Sign up via the API (the UI's web auth has upstream quirks; signup by API
# works reliably and is what the helper script automates):
EMAIL='you@example.com'
PW='PickARealPassword!'
curl -s -X POST http://localhost:8711/auth/signup \
  -H "Content-Type: application/json" \
  -d "{\"formFields\":[{\"id\":\"email\",\"value\":\"$EMAIL\"},{\"id\":\"password\",\"value\":\"$PW\"}]}"

# Mint a session JWT via SuperTokens core (this is what ~/bin/gogett-token.sh does):
GOGETT_PASSWORD="$PW" ~/bin/gogett-token.sh
# Use the printed access_token as LEMMA_TOKEN with lemma-cli.
```

### 12. Use the web UI

Open `https://gogett.webrnds.com` in any browser. Sign in with the email +
password from step 11. You'll land in the org-creation flow, then create
your first pod.

## Maintenance

- **Cert expiry:** `~/.cloudflared/cert.pem` is valid 90 days. Re-run
  `cloudflared tunnel login` when it expires.
- **Cloudflared upgrade:** `brew upgrade cloudflared` is fine — the tunnel
  config + credentials are independent of the binary.
- **Lemma upgrade:** `git fetch upstream && git merge upstream/main` (no
  conflicts expected because all gogett edits are brand-only).
- **Frontend rebuild:** after any change to `lemma-frontend/`, run
  `~/bin/rebuild-frontend.sh`.

## Known limitations (upstream)

- **Web UI over the public tunnel** works for sign-in / sign-up, but the
  upstream `lemma-frontend` build does not set `Cookie: Domain=` for
  SuperTokens session cookies when reached via the Next.js rewrite. Auth
  state therefore has to be re-established on each browser refresh.
  Token-based access via the CLI + `LEMMA_TOKEN` is unaffected.
- **`lemma-stack restart` overwrites the frontend container.** After any
  `lemma-stack restart`, re-run `~/bin/rebuild-frontend.sh` to bring the
  custom image back.
- **Email transport** is `filesystem` (writes to
  `~/.lemma/local/state/emails/`). There's no SMTP wired up — password
  resets require reading the email file directly.