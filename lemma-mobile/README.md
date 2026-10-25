# Lemma mobile

The phone app, in the Lemma-first direction of the web's `/o` frame: three
tabs, **Home · Lemmas · Apps**. Expo (SDK 57), TypeScript, Expo Router, for
Android and iOS from one codebase.

| Screen | Where |
|---|---|
| Home: what needs you, answered in place; the ask box with its Lemma picker | `src/app/(tabs)/(home)/index.tsx` |
| Needs you, all of it: filters, then Behind / Today / Earlier | `src/app/(tabs)/(home)/needs.tsx` |
| Lemmas: the web sidebar, full width | `src/app/(tabs)/lemmas.tsx` |
| A Lemma's page: owns line, its number, Recently | `src/app/lemma/[podId].tsx` |
| A chat: the web's thread for a phone (PARITY.md, A): cards, approvals and questions docked above the box, attachments, the model | `src/app/(in)/chat/[podId]/[id].tsx` |
| New chat, and starting one with people | `src/app/new-chat/[podId].tsx` |
| A shared chat, a person's line, a contact waiting on a person | `src/app/shared/[podId]/[id].tsx` |
| Apps, by Lemma; an app full screen | `src/app/(tabs)/apps.tsx`, `src/app/app/[podId]/[name].tsx` |
| Notifications with actions (a mock: there is no push yet) | `src/app/notices.tsx` |

## Run it

From `lemma-mobile/`, with Node 24 and the SDK built (`npm ci && npm run build`
in `lemma-typescript/`, which `lemma-sdk` installs from):

```bash
npm ci
npx expo run:android
```

```bash
npx expo run:ios
```

`android/` and `ios/` are generated (Continuous Native Generation) and not
committed; change native behaviour in `app.json` or `plugins/`. iOS needs Xcode
with the iOS platform installed (`xcodebuild -downloadPlatform iOS`) and CocoaPods.

**Sample data** needs no account: "Look around with sample data" on the
sign-in screen. The fixtures (`src/data/sample.ts`) are raw reads shaped like the
design canvas, run through the same model code as live data.

**A local stack with everything in it.** Run the stack with the mock model
(`E2E_LLM_MODE=mock`), sign up once on its web app, then
`node scripts/seed-local.mjs`. It gives one Lemma tables with linked rows,
files of every kind the viewer handles (markdown, HTML, CSV, JSON, PDF, PNG,
one in `/me`), a workflow waiting on your form, a schedule, a group, chats
paused on an approval and on a question, and a chat showing a widget, a table
and a file. It adds only what is missing, so run it again at any time. Point
the app at the stack with "Change" on the sign-in screen: Custom, the API
`localhost:8761` and the web app `localhost:3761` on an emulator, after
`adb reverse` for both ports.

**Checks:** `npm run check` (types, then the model tests in `tests/`).

A release APK, signed with the debug key, for installing by hand:

```bash
cd android && ./gradlew app:assembleRelease -PreactNativeArchitectures=arm64-v8a,armeabi-v7a
```

It lands in `android/app/build/outputs/apk/release/app-release.apk`.

**TestFlight.** Sign in to Xcode (Settings › Accounts) with an Apple ID on the
team, and create the app once in App Store Connect with bundle ID
`work.lemma.mobile`. Then:

```bash
APPLE_TEAM_ID=XXXXXXXXXX scripts/testflight.sh
```

It regenerates `ios/`, archives with Xcode-managed signing, and uploads; the
build number is the time of the build. Nothing in the repo holds a credential.

## Signing in

The app signs in on the web's own sign-in page, in a WebView, so it supports
whatever the web supports there (email code, password). Once `/users/me` answers
in that WebView, a short injected script asks for a session of the app's own
with `POST /auth/cli/session-tokens` (the call `lemma auth login` makes from a
browser) and hands it over. The app keeps it in the keystore, sends it as
`Authorization: Bearer`, and refreshes it with `POST /auth/cli/refresh`
(`src/session/auth.ts`). No backend change was needed.

Two sessions, never one shared: the WebViews keep the cookie session from
signing in (pod apps read the parent-domain cookie, so they open signed in), and
the app has its own header tokens. The phone's tokens are never written into a
cookie: a refresh token held in two places trips SuperTokens' theft detection
and signs both out.

Google refuses to sign anyone in inside an embedded WebView, so "Continue with
Google" goes through the phone's browser instead (`src/session/handoff.ts`). It
reuses the web's desktop handoff as it is, so no backend change was needed:

1. The app opens a request (`POST /auth/desktop/requests`, PKCE challenge) and
   shows its short code. On the next tap it opens
   `{web}/auth?desktop_browser=1&desktop_request=<id>&client=mobile` with
   `openAuthSessionAsync`: a Custom Tab on Android, an auth session on iOS.
2. The person signs in there, with Google or any other way. The page shows the
   same code, names "the Lemma app on this phone" (that is what `client=mobile`
   is for), and asks them to confirm. It then opens `lemma://auth/complete`,
   this app's scheme, so the app comes back to the front. The router is given
   nothing to do with that link (`src/app/+native-intent.tsx`).
3. A hidden WebView trades the verifier at `POST /auth/desktop/session` for a
   cookie session of its own, so pod apps open signed in. The usual mint then
   gives the app its header tokens.

The CLI's loopback (`/auth/cli/login` posting to `127.0.0.1`) was the other
option, and it was rejected. Chrome on Android (checked on Chrome 154) holds a
public page's request to loopback behind "wants to access other apps and
services on this device". A "Block" there breaks sign-in until it is undone in
site settings. That page also cannot bring the app back.

Gaps: the sessions are labelled `lemma-desktop` (WebView) and `lemma-cli`
(app), since the backend has no phone value. Until the web change that reads
`client=mobile` is deployed, the page still says "Lemma Desktop". In the email
WebView, tapping Google switches to the browser, where the person taps Google
again.

**Which server.** lemma.work, a server the build names, or a custom address, chosen under
"Change" on the sign-in screen or under Server on the account screen
(`src/session/server.ts`). Each server keeps its own session, so switching back
signs nobody out. A custom API at `api.example.com` finds its web app at
`example.com`; a bare port is a dev stack on this computer (`10.0.2.2` from the
Android emulator, `localhost` from the iOS simulator). Plain HTTP is allowed only
to local addresses (`plugins/local-cleartext.js` on Android,
`NSAllowsLocalNetworking` on iOS).

A build can offer more servers by name: set `EXPO_PUBLIC_LEMMA_SERVERS` in a
git-ignored `lemma-mobile/.env.local` to a JSON list of
`{ "id", "label", "api", "web" }`, for example
`[{"id":"staging","label":"staging.example.com","api":"https://api.staging.example.com","web":"https://staging.example.com"}]`.
They are listed after lemma.work, and their web links open the app
(`app.config.js`). Expo reads the file for `expo start`, `prebuild` and the
release builds alike. Metro caches the value it inlined, so after changing the
file start it with `--clear`.

## Data

Screens read through one interface, `LemmaSource` (`src/data/source.ts`), with a
live source and a sample one. `src/data/live.ts` reads **main's API as it is**,
per Lemma: conversations, pending asks, workflow waits, channels, contacts
waiting on a person, and tables, for each Lemma, polled once a minute. Its
shapes mirror the org-level reads proposed in #1045 (`needs-you`, `recents`,
`apps`, `home/seen`) and #1046's owns line and number, so when those land only
`live.ts` changes. Until #1046 does, what a Lemma owns comes from `/plan.md`,
parsed as `/o` parses it, and its number from the plan's measure.

**By area.** Everything past Home, Needs you and the Lemmas list is split by
area under `src/areas/<area>/`: chat, tables, files, work, chats, admin and
more. Each has its own reads and writes (`source.ts`, `live.ts`,
`sample.ts`), hooks, routes and its tab on a Lemma's page, and screens reach
it through `useArea("<area>")`. Signed-in screens live in the `(in)` route
group, which the root layout guards as a whole, so adding a screen is adding a
file. [PARITY.md](PARITY.md) lists everything the web does and where each part
stands on the phone.

**Light and dark.** `color` (`src/ui/tokens.ts`) holds the palette in use,
and screens read it as they draw. `ui/theme.tsx` swaps it and the layout
remounts the navigator, which the router reopens where the person was. Use a
token, never a literal colour, and a screen is dark-ready.

The pure logic is ported from the web, not reinvented:

- `src/data/model.ts`: from `lemma-frontend/src/outcomes/model.ts` (PR #1042).
- The web's own pure modules run where they live, imported as `@web/<path>`
  from `lemma-frontend/src` (`web-imports.js`; Metro, vitest and TypeScript
  all resolve it): the thread's turns and approvals, the channel, table,
  file, workflow and settings models. Nothing is copied:
  `tests/web-imports.test.ts` fails on a file in `src/` that is a copy of a
  web module. A web module that cannot run on a phone gets a shim
  (`src/web-shims/`), swapped in however a web file reaches it.
- Streaming is the SDK's `AgentController`, the core the web's
  `useAssistantSession` uses (`src/thread/use-chat.ts`).

## Not built yet

- **Push notifications.** The backend sends none, so `notices.tsx` is the canvas's
  lock screen as a mock, reachable from the account screen.
- **Everything PARITY.md does not mark done.** Each place is reachable: a tab or
  screen says it is on the web for now and opens the workspace page in the app.
- **The rule suggestion** on Needs you shows in sample data only: nothing on the
  server suggests rules.

Fonts are built into the app (`expo-font` in `app.json`), so text is never laid
out in a fallback face and then clipped when the real one loads. A built-in font
goes by its file name on Android and by its PostScript name on iOS
(`AlbertSans-Medium`); `font` in `src/ui/tokens.ts` names each the way its
platform knows it. With the Android name on iOS, headlines clip at the right.

Two traps this avoids:

- A conversation that has never run reports `WAITING`, the same as one paused on
  an ask, so "needs you" also requires a run behind it (`last_run_status` /
  `last_run_finished_at`).
- A chat is created on its first send, never on open: the route id is `new`
  until then.
