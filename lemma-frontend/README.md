# Lemma Frontend

The Next.js web application for Lemma: authentication, the pod workspace, agents,
workflows, desks, data and files, integrations, and product docs.

Built with Next.js 16 (App Router), React 19, TypeScript, and Tailwind CSS.

## Prerequisites

- **Node.js 20+** and npm.
- The **`lemma-sdk` package**, which is built from the sibling
  [`lemma-typescript`](../lemma-typescript) directory of this monorepo. The build
  scripts compile it automatically (see [Project layout](#project-layout)), so
  you need that directory checked out at `../lemma-typescript`.
- A running **Lemma backend** to talk to. By default the app targets a local
  backend (see [Configuration](#configuration)).

## Quick start

```bash
npm install
cp .env.example .env.local   # then edit values as needed
npm run dev
```

The app starts on http://localhost:3000.

`predev` builds the local SDK and regenerates `public/runtime-config.js` from your
`.env.local`, so the browser and server agree on configuration.

## Configuration

All runtime configuration is provided via `NEXT_PUBLIC_*` environment variables.
Copy `.env.example` to `.env.local` and adjust. Leave a value unset to use its
default.

| Variable | Default | Description |
| --- | --- | --- |
| `NEXT_PUBLIC_API_URL` | `https://api.localhost` | Base URL of the Lemma backend API. |
| `NEXT_PUBLIC_SITE_URL` | `http://localhost:3000` | Public URL this frontend is served from. |
| `NEXT_PUBLIC_AUTH_URL` | = `SITE_URL` | URL handling auth flows (usually the same as the site). |
| `NEXT_PUBLIC_SESSION_TOKEN_DOMAIN` | _(empty)_ | Cookie domain for the session. Set to your apex domain (e.g. `.example.com`) only when serving from that domain; leave empty on `localhost`. |
| `NEXT_PUBLIC_SHARED_SESSION_DOMAIN` | _(unset)_ | Apex domain to share a login across sibling subdomains (e.g. desk apps). Leave unset for single-host cookies. |
| `NEXT_PUBLIC_APPS_DOMAIN_SUFFIX` | _(unset)_ | Domain suffix under which pod desk apps are served (e.g. `apps.example.com`). Optional. |
| `NEXT_PUBLIC_SUPPORT_EMAIL` | `deepak@lemma.work` | Contact address shown on legal pages and support links. |
| `NEXT_PUBLIC_APP_NAME` | `Lemma Auth` | Display name used by the auth portal. |

These values are read at runtime (not baked in at build time), so the same build
can be deployed to multiple environments. In production they are injected into
`public/runtime-config.js` by `docker-entrypoint.sh`; in development by the
`gen:runtime-config` script. If you change `.env.local` while `npm run dev` is
running, re-run `npm run gen:runtime-config` and hard-refresh the browser.

## Project layout

```
app/         Next.js App Router routes (auth, pod workspace, docs, legal, …)
components/  React components, grouped by feature area
lib/         Data hooks, auth/config, SDK glue, utilities, types
styles/      Global and shared CSS
scripts/     Build and CI helpers (runtime config, design-system audit, …)
public/      Static assets
```

The app depends on the local `lemma-sdk` package (`lemma-sdk: file:../lemma-typescript`
in `package.json`). The `predev`, `prebuild`, `prelint`, and `pretypecheck`
scripts run `npm --prefix ../lemma-typescript run build` to compile it first, so a
full monorepo checkout is required to build this package.

## Verification

```bash
npm run check   # design-system audit + ESLint + TypeScript + edu-anchor checks
npm test        # Vitest unit tests
npm run build   # production build (builds the local SDK first)
```

`npm run check` is what CI runs; run it before opening a pull request.
Vitest discovers test and spec files in `app/`, `components/`, and `lib/`,
including TypeScript JSX. Tests run in Node by default; component interaction
tests opt into jsdom with a per-file environment directive.

## Production / Docker

A `Dockerfile`, `docker-compose.yml`, and `docker-entrypoint.sh` are provided. The
build produces a Next.js standalone output (`output: "standalone"`). At container
start, `docker-entrypoint.sh` generates `public/runtime-config.js` from the
container's `NEXT_PUBLIC_*` environment variables and launches the server, so you
configure a deployment purely through environment variables.

```bash
docker compose up --build
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Apache License 2.0. See [LICENSE](LICENSE).
