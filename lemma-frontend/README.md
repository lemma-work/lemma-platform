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

## Conversation loading

Opening saved history reserves the transcript area with a delayed, gently pulsing
skeleton using the same avatar gutter, right-aligned user bubbles, and teammate
reply cards as loaded messages. New conversations show the welcome state immediately. Background reads
keep existing messages visible; failed history loads offer Retry and preserve the
composer draft. The skeleton respects reduced motion and announces loading to
assistive technology. In development, `/demo/loading` previews loading, empty,
failed, populated, and workspace states using the real transcript and composer
components. Workspace bootstrap and session gates share a workspace skeleton;
local opening actions use the same accessible progress indicator. History lists
use row placeholders rather than standalone loading prose.

## Session recovery

An unsuccessful return from sign-in offers Sign in again and Back to home, without
assuming cookies caused the failure. Each explicit retry counts as a portal trip
so an unsuccessful retry cannot trigger another automatic redirect. The portal
and workspace use the same bounded session-refresh retry limit.

Initial cookie discovery cannot override later authentication events or replace
an explicitly configured bearer identity. Account changes clear cached workspace
data and saved organization/tab locations while preserving appearance settings;
refreshing the same account retains its saved locations. Other open tabs reload
when the workspace account or configured credentials change. Sign-out reports an
unconfirmed server session instead of silently returning home.

Run `node --experimental-strip-types --import ./tests/resolve.mjs --test tests/observe-auth.test.ts tests/storage.test.ts tests/door.test.ts`
for frontend lifecycle checks, and `npx vitest run src/__tests__/auth.test.ts` in
`lemma-typescript` for SDK request-race and sign-out checks.

## Linked teammate access

Opening a teammate link verifies that pod directly, independently of the cached
organization list, and selects its organization from the response. An incomplete
or stale list is not an access denial. Verification failures offer Retry; only
an explicit forbidden response offers Ask to join. The workspace never substitutes
another teammate for the one named in a link.

Run `node --experimental-strip-types --import ./tests/resolve.mjs --test tests/pod-access.test.ts`
for the access-state regression checks.

Approved chat cards collapse to an action and approval-scope row. Expand the row
to inspect the original explanation and command arguments. Pending requests keep
the full decision controls visible, and submitted approvals retain their waiting
status until the tool finishes.

Header channel actions with labels keep their natural width; folded teammate
names retain enough line height for descenders while long names still truncate.

Embedded demo documents leave analytics and consent to their containing page.
Standalone demos show the same analytics choice as other top-level pages.
Demo teammate access resolves against Acme sample data without authentication.

## Channel setup and management

Open **Manage channels** beside a teammate's contact channels or in its profile.
The header and its sheet show only the pod responder's channels. Other agents'
channels appear within their individual profiles. Each keeps one connection per
platform and lists unfinished and disabled connections so setup can be resumed. **Manage** opens the provider's setup
checklist, webhook values and administrator consent, plus responder selection,
Slack/Teams channel selection, email sender filters and the existing-thread send
policy. Refresh setup after completing steps with the provider.

Custom Telegram and WhatsApp accounts use the deployment's credential schema;
managed Telegram creation and OAuth remain available where supported. Secrets
are held only in the current form, and setup secrets are masked until revealed.
Configuration edits send only the fields the form owns, preserving other
provider and conversation settings.

Run `npm test` for catalog, configuration and setup rendering regressions.
Provider consent and message delivery require a connected workspace to verify.

Workspace skeletons are reserved for authenticated workspace data. Auth and demo
transitions use contextual status messages. Settings omits the Help section and
clips its sidebar to the panel corners. Message copy controls appear on hover or
keyboard focus over the bottom-right edge without reserving layout space.
