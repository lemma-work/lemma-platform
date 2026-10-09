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

See [.env.example](.env.example) for configuration. Voice calls require the
server-only `GEMINI_API_KEY` (or `OPENAI_API_KEY` with
`NEXT_PUBLIC_VOICE_PROVIDER=gpt-live`); never expose secrets through
`NEXT_PUBLIC_*` variables. What a caller says is routed by the backend's
decisions API through the SDK, so routing has no key here: the backend's
`DECISION_PROVIDER` decides, and `typesafe` is the fast one (see
[Decisions](../docs/configuration.md#decisions)). `/auth` provides sign-in;
`/connect` supports manual token sign-in.

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

## Side-by-side views

Clicking a resource tab opens it in the right sidebar alongside the
conversation. Opening a file or table from Library keeps Library on the left;
opening a row from a table keeps that table on the left. Closing the right
pane returns to that source view. Profile and apps open full-width — an app
squeezed into the sidebar reflows into something cramped, so it never opens there. The top-right “View in full” control expands that resource;
“Return to sidebar” restores the conversation beside it. Selecting another
tab opens that tab in the sidebar. Selecting Conversation closes the sidebar.
Drag the divider to resize the sidebar; its width is remembered in local storage.
The divider also supports Left/Right arrow keys, Home/End, and double-click to
reset. The divider uses a single line, highlighted on hover, drag or keyboard focus.
Documents use the warm paper surface in both themes, matching their sidebar toolbar.
Chat uses compact horizontal gutters. Resource toolbars omit the Ask action.
File editors and the conversation retain their mounted state when
expanding or returning. Below 768px the views stack vertically.

Run `node --experimental-strip-types --import ./tests/resolve.mjs --test tests/split-tabs.test.ts`
for pane-selection regressions. For a browser check, open `/demo/landing`,
open a file, enter a conversation draft, expand the file and return
it to the sidebar. Verify that the draft and file state survive, and that
Launch studio opens full-width with no divider.
Repeat at 1440, 1024, 768 and 375px.

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
Feature flags (`groups`, `contacts`) come from PostHog and are off wherever it
does not run; `NEXT_PUBLIC_LEMMA_FEATURES=groups,contacts` turns them on
regardless.

Run `npm run check`, `npm test`, and `npm run build` for the component gates.
With Node dependencies installed, the public website scenarios build and boot
the frontend themselves: from `tests/scenarios`, run
`uv run pytest journeys/public_website -q`. They use no backend credentials.

## Conversation loading

The Computer view shows workspace download and startup progress and refreshes
files when the workspace becomes ready. Its browser accepts native paste and
can float in a separate window where Document Picture-in-Picture is supported.
Closing the window returns the browser to the pane; leaving Computer closes
the viewer. Teammates share the person's browser and saved logins.
In development, `/demo/computer` previews startup states and the floating-window
control using sample content without connecting to a workspace.

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

Email code is the default on both sign-in and sign-up. The browser-bound
`/auth/email-code` flow creates or reuses the canonical account after the code
is verified, then resumes the saved destination. Codes expire after ten minutes;
the backend enforces the attempt and resend limits. Password and provider login
remain available. The API must allow the frontend origin for email-code requests.

The auth portal keeps the requested destination through sign-in, sign-up,
provider callbacks, password reset, and email verification. It accepts this
site, the configured API origin, and configured pod-app hosts; auth routes and
untrusted origins cannot be return destinations. Without a destination, users
land at `/t`. Email links opened in another browser have no saved destination;
the original tab retains it.

After authentication, the API decides whether the account needs verification.
Unverified accounts receive a verification email before entering the workspace.
The waiting tab detects verification completed in another tab; Continue refreshes
the session and resumes the requested destination. Existing sessions follow the
same access check when they visit sign-in.

Run `npm run test:auth-browser` for browser regressions against isolated auth API
fixtures. Install Chromium with `npx playwright install chromium`, or set
`LEMMA_TEST_BROWSER_CHANNEL=chrome` to use installed Chrome. These checks exercise
the real portal and SuperTokens browser client; live email delivery and provider
consent still require deployment verification.

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

Workspace skeletons are reserved for workspace data that exists: an
authenticated workspace, or the landing demo's sample one. Auth transitions use
contextual status messages. The landing hero draws the skeleton itself, so it is
there on first paint, and keeps it over the demo frame until the workspace
reports it has a teammate and conversations to show. Settings omits the Help section and
clips its sidebar to the panel corners. Message copy controls appear on hover or
keyboard focus without reserving layout space: beside your bubble, and inside the
top-right corner of a teammate's message. Neither straddles an edge.

The linked SDK and workspace share one React Query module through the bundler
aliases in `next.config.ts`. To check that SDK table hooks see the workspace cache,
run `node --test tests/browser/query-context.test.mjs` against a running development
server (default `http://localhost:3000`, override with `LEMMA_TEST_ORIGIN`). The
`/demo/query-context` fixture uses cached data and makes no API requests; it is
unavailable in production.
