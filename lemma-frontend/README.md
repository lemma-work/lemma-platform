# lemma-frontend

Lemma’s user-facing Next.js app: conversations, teammate apps, files, workflows,
and voice calls. Uses `lemma-sdk` to connect to the platform API.

## Development

Requires Node.js 24 (see the root `.nvmrc`).

```sh
npm --prefix ../lemma-typescript ci
npm ci
cp .env.example .env.local
npm run dev
```

Open http://localhost:3000. Set `NEXT_PUBLIC_API_URL` for live data, or
`NEXT_PUBLIC_DATA=sample` for a local demo without a backend.
The sibling `lemma-harness` provides operator tools and the desktop web runtime.

See [.env.example](.env.example) for configuration. Voice calls require
server-only `GEMINI_API_KEY` and `TYPESAFE_API_KEY`; never expose secrets through
`NEXT_PUBLIC_*` variables. `/auth` provides sign-in; `/connect` supports manual token sign-in.

## Checks

```sh
npm run typecheck
npm test
npm run build
```

Build runs lint, naming and design checks before compiling. Some tests bind
local WebSocket ports. Design conventions are in [DESIGN.md](DESIGN.md).

## Structure

- `src/app/`: Next.js routes and API handlers.
- `src/data/`, `src/session/`: sample/live data and authentication.
- `src/shell/`, `src/stage/`, `src/thread/`: workspace and conversations.
- `src/call/`, `server/`: voice routing and WebSocket gateways.
- `src/marketing/`: landing previews and sample apps.
- `src/styles/`: shared tokens and feature styles.

Keep the conversation mounted while changing stage tabs; open apps stay alive
when hidden. Use `pod` for API entities and “teammate” in the interface.

## Deployment

Run `npm run build`, then `npm start` (the custom `server.mjs`, not
`next start`). Hosting needs a persistent Node process, WebSocket upgrades,
and proxy timeouts of at least 15 minutes. `PORT` controls the listening port.
`NEXT_PUBLIC_*` values are fixed at build time; server keys are runtime settings.

Build the container from the repository root:

```sh
docker build -f lemma-frontend/Dockerfile --build-arg NEXT_PUBLIC_API_URL=<api-origin> .
```
Release builds use the `FRONTEND_API_URL`, `FRONTEND_AUTH_URL` and
`FRONTEND_APPS_DOMAIN_SUFFIX` repository variables.

## Public website

The main frontend serves company and legal pages, documentation, templates,
GitHub bundle imports, downloads, the blog and changelog, public share/contact
links, and compatibility routes for older workspace URLs. Public content lives
in `src/site/data` and `content/`; it is rendered on the server. The developer
site draft remains a separate app.

`/llms.txt` indexes the website. The homepage, documentation, company pages and
legal pages also support `Accept: text/markdown` and set `Vary: Accept`.
`npm run sync:openapi-spec` generates the public API specification from the
Python SDK's committed public spec; `npm run check:openapi-spec` checks freshness.

`NEXT_PUBLIC_SITE_URL` sets the canonical origin at build time. Analytics uses
`NEXT_PUBLIC_ANALYTICS_KEY`, `NEXT_PUBLIC_ANALYTICS_HOST`, and
`NEXT_PUBLIC_LEMMA_DEPLOYMENT` from the runtime public configuration endpoint;
local deployments and deployments without a key do not initialize analytics.
The ingestion and asset proxy hosts are configurable at build time using
`NEXT_PUBLIC_ANALYTICS_INGEST_HOST` and `NEXT_PUBLIC_ANALYTICS_ASSETS_HOST`.
Only explicitly allowed event properties and redacted route templates leave
the app; DOM autocapture and replay are disabled. Storage consent is remembered.

Run `npm run check`, `npm test`, and `npm run build` for the component gates.
With Node dependencies installed, the public website scenarios build and boot
the frontend themselves: from `tests/scenarios`, run
`uv run pytest journeys/public_website -q`. They use no backend credentials.
