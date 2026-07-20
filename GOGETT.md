# gogett-hub

A gogett-branded deployment of [lemma-work/lemma-platform](https://github.com/lemma-work/lemma-platform),
exposed at **`https://gogett.webrnds.com`** via Cloudflare Tunnel from a
single Mac.

## TL;DR

```bash
# Install + start the stack
brew install uv cloudflared docker
uv tool install --from ./lemma-stack lemma-stack

# Authorize Cloudflare, create tunnel, route DNS
cloudflared tunnel login
cloudflared tunnel create gogett
cloudflared tunnel route dns --overwrite-dns gogett gogett.webrnds.com

# Install stack, override env, build the custom frontend
PATH="$(uv tool dir)/bin:$PATH" lemma-stack install --runtime auto -y
# ... (see docs/gogett-deploy.md for all the config set commands)
~/bin/rebuild-frontend.sh

# Open https://gogett.webrnds.com
```

## What this fork is

The upstream `lemma-platform` is a great OSS stack (pods, agents, workflows,
RAG, functions, multi-tenant). We want to host it under the **gogett**
brand on a single Mac, reachable from a phone over the public internet.

This fork:

- Keeps every `lemma-*` package name, install URL, and runtime identifier
  unchanged. **`git fetch upstream` stays clean.**
- Patches only the brand layer (README, landing HTML, desktop UI copy,
  support email) and the parts required for non-localhost deployment
  (Next.js rewrites, CORS, cookie domain).

## Full setup guide

→ **[docs/gogett-deploy.md](docs/gogett-deploy.md)**

## Files in this repo that are gogett-specific

| Path | Change |
|---|---|
| `README.md` | Brand copy, signup link, support email → `gogett.webrnds.com` / `info@webrnds.com` |
| `lemma-frontend/README.md` | Support email `info@webrnds.com` |
| `lemma-frontend/next.config.ts` | Adds `rewrites()` proxying `/st/auth/*` and `/api/v1/*`, `/users/*`, `/organizations/*`, `/pods/*` to the backend |
| `lemma-frontend/public/landing-page/*.html` | Demo hostnames → `gogett.webrnds.com` |
| `desktop/ui/index.html` | Cloud chooser copy → `gogett.webrnds.com` |
| `docs/gogett-deploy.md` | Full reproduction guide for this deployment |
| `GOGETT.md` | This file |

## Helper scripts (in `~/bin/`)

| Script | What it does |
|---|---|
| `gogett-token.sh` | Mints a fresh SuperTokens JWT for `d.a.schreuder@gmail.com` so `lemma-cli` can talk to the local stack |
| `rebuild-frontend.sh` | Rebuilds `lemma-frontend-gogett:local` from source and restarts the frontend container |
| `gogett-restart.sh` | Wrapper: `lemma-stack restart` + rebuilds the custom frontend so the MCP rewrites survive |

## Status

- Stack running, tunnel alive, public URL serving 200 OK.
- Brand layer complete; full upstream feature parity (pods, agents,
  workflows, MiniMax-powered agents).
- One user account + org + pod pre-created.

## License

Same as upstream (Apache-2.0 for the SDKs, AGPLv3 for the core). See
`LICENSE` and `LICENSES/`.