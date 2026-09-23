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
