# Lemma Desktop: app experience

Status: implemented.
Scope: macOS local mode. Windows inherits everything except the AppKit-specific
items in §2 and §5.
Companions: [product spec](local-desktop-product-spec.md),
[technical design](local-desktop-technical-design.md).

The 0.7 Desktop was correct: it installed, supervised, healed, and stopped
cleanly. What it was not yet was an *app*. Eight things separated it from one,
each with a concrete cause in the code rather than a vague feeling. This records
what each one was and what was done about it. Items 1–5 came from reading the
shipped experience; item 6 and the corrections inside §4 came from installing
the build and using it, which is the only way most of them could have surfaced.

| # | Complaint | Root cause | Outcome |
| --- | --- | --- | --- |
| 1 | Slow to open after the first time | Every launch replayed splash → start → health gate → full web boot | Optimistic resume; ~4 s of redundant health gate deleted; launch trace |
| 2 | Black screen on close | Quit blocked the main thread for up to 120 s with a dead window on screen | 5 s bound, windows hidden first, window layer painted |
| 3 | Marketing landing page inside the app | `RootPageSwitch` rendered `LandingPage` for any unauthenticated visitor | Never rendered for a local deployment, browser visitors included |
| 4 | AI did not work after setup | Two unrelated provider systems; onboarding wrote the one the health probe cannot see | One inline step covering agents and providers, answered in place |
| 5 | Settings and menus fought the product | `control.html` was a second design system; there was no app menu at all | Real menu bar with ⌘,; tray cut to a glance and four verbs; control page on the token layer |
| 6 | Chatting with a local agent hit a macOS "Keychain Not Found" modal | Packaged backend told to use the keychain provider while running with `HOME` pointed at an app-owned directory | locald mints the keyset in its own vault and hands it over |
| 7 | Local agents never answered; chat sat at "Thinking" | A failed harness publish rescheduled itself fifteen minutes out, so commands were rejected for naming an unpublished harness | Retry in seconds, and publish on demand when a command needs one |
| 8 | A signed-out agent reported an internal error; an installed agent reported as missing | Adapter errors forwarded verbatim; the executable search list did not know where agents' own installers put them | Recognise authentication failures by name; search `~/.opencode/bin` and friends |

---

## 1. Open instantly after the first time

### What used to happen on every launch

Local mode, second launch, everything already warm:

1. `main()` built the window at `ui/index.html` — the native splash — and showed
   it.
2. `ensure_locald` connected to the surviving daemon socket.
3. `start_impl` sent `{"cmd":"start"}`.
4. The daemon ran `start_host_packs`, probed the VM, then `start_all_inner`.
5. `start_all_inner` took its already-running branch and re-ran
   `wait_process_health` for backend and frontend — each carrying
   `stabilization_seconds: 2`, serialized — and then immediately ran
   `verify_all_health_now`, which is the same probes with stabilization set to
   zero.
6. `ready` arrived; the shell navigated from the splash to the workspace URL.
7. WKWebView cold-loaded Next.js at `/`, which authenticated, resolved the last
   pod, and `router.replace`d into `/pod/:id`.

Nothing in that sequence was cached across launches. The splash was always paid
for, the health gate was always paid for, and the web app always booted twice.

### What happens now

**Warm health gate deleted** ([host_process.rs](../../desktop/locald/src/host_process.rs)).
In the already-running branch, the per-service `wait_process_health` call is
gone and `verify_all_health_now` — same probes, same retry budget, no
stabilization dwell — carries it. The dwell exists to catch a process that dies
seconds after it starts listening; one that has been serving since the last
session has already proven that. The cold path still pays it. Worth ~4 s.

**Optimistic resume** ([main.rs](../../desktop/src/main.rs)). The shell records
the serving workspace's `url`, `api_url`, and runtime generation in
`desktop-config.json` whenever a `ready` event lands. At launch, before the
window is created, it asks `{api_url}/health/ready` with a 250 ms timeout and
requires both a 2xx and the recorded generation back. On a match the window is
built directly at the workspace, and `ensure_locald` plus the `start` reconcile
run on a worker so nothing holds up a window the user can already see. On a miss
it costs 250 ms and lands on exactly the splash path it replaced.

The generation match is what makes this safe. A 2xx alone would also come from
an unrelated listener that took the port after a crash, or from a stale process
of a previous runtime; the generation is minted per user start and handed to the
backend as `LEMMA_RUNTIME_INSTANCE_ID`, so it matches only when this is the
stack the last session left behind.

**Route resume.** The route is captured off the live webview on window close and
at exit, and a resumed launch opens it rather than the root — the root only
authenticates and redirects to the last pod, so using it means loading the app
twice to arrive where the route goes directly.

**What a resume must not do is strand the window.** The first build of this
shipped a blank app, and the launch trace is what named it:

```text
0ms    process start, mode=local
140ms  resume: hit, opening the workspace directly
344ms  window shown
```

…followed, twenty-three seconds later, by the install log unpacking a new
runtime. Installing a new build over an old one leaves the previous stack
serving, so the probe passed against *its* backend, the window opened *its*
workspace, and then `ensure_locald` found a daemon that did not match the new
host pack, replaced it, and brought every service back on new ports. The window
was left on a port nothing was listening on, and nothing ever moved it: the
`ready` navigation only fired when the window was showing the splash, which by
then it was not.

Three things were wrong and all three are fixed:

- **A resume target belongs to a release.** It records the Desktop version that
  wrote it, and a different version refuses it outright. A target written before
  this existed has no release and is refused too, which is what makes an install
  carrying the broken state heal itself.
- **The probe checks both halves.** The window opens the frontend; checking only
  the backend accepted a resume whose frontend had gone.
- **`ready` rescues a stale window.** The test is no longer "is this the
  splash?" but "is this the current workspace?" — so a window showing a
  *different* workspace origin gets navigated, while a stable one is still never
  disturbed, as product spec §3.5 requires.

And if connecting invalidates the window before `ready` arrives, the resume
worker hands it the splash, which is the surface that reports the restart it is
waiting for.

**Services boot alongside each other.** The cold path — what you pay after a
quit — spawned a service, waited for it to pass its *full*
health gate, and only then spawned the next. Measured on a real install:

```text
vm 1.2s · images 0.4s · postgres 1.7s · redis 1.0s · supertokens 5.0s
migrations 1.4s · backend 7.2s · frontend 2.7s        = 20.6s
```

The frontend's 2.7s was on the critical path for no reason: `next start` serves
a prebuilt app and does not wait on the backend, and its health check reads a
static file it serves itself. Spawning now happens for every service up front
and health-gating afterwards, in the same dependency order — which is also how
the supervision loop already read `dependencies`, as "its process must exist",
not "it must be healthy". Readiness is unchanged; the frontend simply boots
while the backend does. About 2.7s off 20.6s.

Holding a spawn failure rather than returning it keeps that from costing
diagnosis: a service that started and then died still reports its exit status
and log first, because the process that crashed is nearly always a better answer
than the one that could not start because of it.

The remaining cold-start costs are the backend's own 7.2s (Python import plus a
2s stabilization dwell) and SuperTokens' 5.0s inside the guest. Neither is
reachable from here.

**Launch trace.** Process start, the resume decision, window shown, and daemon
ready are appended to `runtime/launch.log`, exposed as the **Launch timing**
diagnostics source. "Opens instantly" is not a claim anyone can check by feel;
a resumed launch and a splash launch look identical in a screen recording once
both have finished.

Targets are recorded in product spec §7, replacing "warm application restart:
4 seconds or less", which had no evidence behind it.

---

## 2. Black screen on close

Two distinct paths, fixed differently.

**Quit blocked the main thread.** `RunEvent::Exit` called
`release_before_exit()`, which blocked on a locald round trip with a
**120-second** timeout. Tauri has already torn the webview down by then, so the
window sat on screen as a dead surface while the process was unresponsive to the
window server. Now: `RELEASE_ON_EXIT_TIMEOUT` is 5 s, every window is hidden
before the release starts, and a test pins the bound — the timeout is literally
how long an unresponsive window can stay in front of someone, so it is not a
number to let drift. locald is durable and owns its own cleanup; the shell asking
nicely is a courtesy, not a guarantee.

**No window background.** The builder set no `background_color`, so any frame in
which the webview was not painting showed the native window background — during
navigation, hide/show, teardown. The window now paints `--bg-canvas`, corrected
to the real appearance immediately after build and updated on `ThemeChanged`; a
window whose layer stays light while the page goes dark is the same bug with the
colours swapped.

Recorded for the next reader: `macOSPrivateApi: true` is **not** a cause. tao
only calls `setOpaque(false)` when `transparent` is also set, and `transparent`
is behind `LEMMA_DESKTOP_VIBRANCY`. But if vibrancy ever ships on by default,
the window background stops being cosmetic — every surface that must stay opaque
has to paint its own first.

**Quitting said the wrong thing, and did the wrong thing.** Choosing **Quit and
stop Lemma** put up "Starting Lemma. Preparing local services 6%" and then
exited. The splash reloads as a fresh document with no memory of why it was
opened, so it asked for state, found nothing running, and concluded it was meant
to *start* Lemma — showing that, and actually issuing a `start` that raced the
stop it was displaying. It now opens with `?intent=stop`, which makes it say
"Winding down" and never offer to start what is being stopped.

**Close semantics** are otherwise unchanged: `CloseRequested` → `prevent_close`
+ `hide` is right for macOS. It now also records the workspace route on the way out,
since closing the window is the most common way a session ends and the last
chance to read the route off a live webview.

---

## 3. Never the landing page in local mode

`RootPageSwitch` rendered `<LandingPage />` for any unauthenticated visitor. In
desktop local mode that is the marketing page — hosted pricing, hosted CTAs, a
hosted signup — as the first screen after a multi-minute local install. It was
reachable three ways: the `ready` navigation targeted `/`, the tray's **Lemma
Home** resolved to `/`, and any sign-out returned there.

The rule is unconditional: in local mode the landing page never renders, in any
state, for any visitor.

The switch now branches on `isLocalDeployment()` and redirects to
`/auth?show=signup`. Signup rather than sign-in, because an install with an
account to sign into would not have sent them to the bare root.

The important part is *what* it branches on. A LAN or public-link visitor
arrives in an ordinary browser with no `__LEMMA_DESKTOP__` global, so the shell
marker does not cover them. locald sets `NEXT_PUBLIC_LEMMA_DEPLOYMENT=local` on
the frontend process it supervises
([native_host_pack.rs](../../desktop/locald/src/native_host_pack.rs)), surfaced as
`config.DEPLOYMENT` / `isLocalDeployment()` in
[lib/config.ts](../../lemma-frontend/lib/config.ts). That marks the deployment,
not the client, so all three visitors are covered by one branch.

The shell also stopped aiming at `/`: on `ready` it opens the recorded route.

Guarded by [local-deployment.test.ts](../../lemma-frontend/lib/desktop/local-deployment.test.ts),
which pins the single guarded call site — this is a wiring mistake that
typechecks perfectly and shows up only as a pricing page inside someone's
desktop app.

---

## 4. Onboarding owns the local setup

### The bug underneath it

There were two unrelated AI provider systems, and the one onboarding wrote was
invisible to the check that gates agents.

| | Desktop **Local settings** | Web **onboarding → connect** |
| --- | --- | --- |
| Storage | locald `OperatorConfigStore` + OS keychain | custom provider profile row, per organization |
| Effect | `LEMMA_OPENAI_*` / `LEMMA_ANTHROPIC_*` env, restarts the backend | database row, live immediately |
| Drives `ai_profile` health? | Yes, via `LEMMA_LOCAL_AI_READY` | No |

So a local user could finish onboarding, connect OpenAI, and still be told
**Configure an AI provider** forever. Worse, `setupStepsForAudience("personal")`
returned `["identity", "start"]` — solo users, which is most people installing
Lemma on their own Mac, never saw the provider step at all.

### One step, answered in place

`LOCAL_SETUP_STEPS` is `identity → intelligence → sharing → start`, used
regardless of audience. `normalizeOnboardingStep` sends a draft naming a step
this flow does not have back to `identity`, so a draft written against hosted
Lemma by the same account cannot strand someone on a screen that never renders.

**Intelligence** is one screen answering one question — what should answer in
chats — with both of its answers on it: a coding agent already on this Mac, or
an API provider. An earlier pass made these two steps and sent the provider half
to Local settings to be completed. That respected the security boundary and was
the wrong product: onboarding cannot ask "which model?" and then open a
different window for the answer. The boundary moved instead (below).

**Model selection.** This did not exist. The form was a `<textarea>` taking "one
model per line" plus a free-text default — the user was expected to know their
provider's model ids from memory, and a correct provider could be applied with a
model it does not serve. `config.discover-models`
([operator_config.rs](../../desktop/locald/src/operator_config.rs)) runs the same probe
`apply` runs with no write behind it. Connect, list, pick. Local runners —
Ollama and LM Studio, no key and no account — lead the presets.

**Agents connect themselves.** Pairing exists because a workspace can drive
agents on machines it does not run on: name the computer, mint a code, carry it
over. None of that applies here — the workspace *is* this machine, and the Agent
Host is a sidecar the app supervises — so asking the user to press **Connect
this computer** was asking for consent already implied. It also read as broken,
because pairing takes a moment and the harness scan takes longer still: the
honest-looking result of pressing it was nothing, then nothing, then "no agents
found", and only then the macOS file-access prompt that discovery actually
needed. `useAutoConnectThisComputer` now enables, pairs, and scans on any
authenticated local page, so that prompt arrives while the user is still in
setup and the step opens with a list rather than a button. What is left to ask
is the only real question: which of these do you want in your chats.

The empty state names which of three things is happening — connecting, scanning,
or finished having found nothing — instead of showing "none found" for all
three. Adding an agent flips its row to **Added as …**, read from the managed
profile listing the save already invalidates.

**Who can reach this installation.** Onboarding asks, and anything other than
**This computer** hands off to Local settings, because LAN needs an interface
choice and public needs a fresh confirmation. Those remain writes the workspace
cannot make.

**The banner** no longer asks for a provider when a coding agent is configured.
It reads the capability probe, which only knows the operator config, so someone
who connected Claude Code — a working agent, no provider — used to be told
forever to configure one.

### Why the model can be set here but sharing cannot

`capabilities/control.json` grants the privileged commands only to the bundled
control webview. That boundary is intact for everything that reconfigures the
host — sharing, tunnels, runtime, diagnostics, and `apply_operator_config`,
which takes a whole configuration and could therefore reset unrelated sections.

Two narrow commands were added to the workspace capability:
`discover_provider_models`, which reads nothing and writes nothing, and
`configure_ai_provider`, which reaches `config.set-ai` — a daemon command that
merges *only* the AI section and cannot express any other change. The origin
allowlist is unchanged, so a LAN or public-link visitor still matches nothing,
and `the_workspace_origin_reaches_local_settings_and_nothing_else` pins the
whole granted set so widening it again has to be deliberate.

Both commands answer the call that made them rather than broadcasting on the
event stream, so the page gets the model list, and a real failure message,
instead of polling and guessing.

### Cross-cutting

1. **Required, with an honest escape.** Product spec §3.3 correctly says a
   missing AI profile must not block *signup*. Onboarding is after signup and is
   where this belongs, so the step is part of the flow with a **Continue** that
   advances either way and says plainly when nothing was set.
2. **Survive the restart.** Applying a provider restarts the backend. The button
   says so and waits for the real answer rather than falling back to the splash.
3. **Everything set here stays editable in Local settings.** Onboarding is a
   first pass over the same values, never a parallel store.


## 5. Menus, and Local settings inside the pod design

### 5a — Local settings

`control.css` had its own gold accent, its own paper, its own display face, and
its own radius scale. The separate webview is a security boundary worth keeping;
a second visual identity was never part of that.

The file now opens with one token block carrying the product's values — warm
paper `#f2efe7` / `#131311`, ink `#17181a`, the action violet as `--accent-rgb`,
alpha hairline rules, the badge-pair state colours, 8 px controls and 14 px
cards — and every rule reads from it. The primary button is violet rather than
ink. Bricolage is gone. `local_settings_declares_no_palette_of_its_own` fails
the build on the next raw hex, so this cannot quietly drift back.

One divergence remains and is recorded in the file: the product sets Inter, the
app's CSP forbids fetching it, and no Inter file is bundled in `ui/vendor/fonts`.
The stack asks for Inter and falls back to the system face. Bundling the woff2
closes it.

The presentation stays in the bundled page rather than moving into the pod shell
behind a narrow audited bridge. That remains the better end state and is worth
revisiting; it re-opens the security model and is a much larger piece of work.

### 5b — Menus

There was no app menu at all — the shipped build used Tauri's default, so there
was no ⌘, and every Lemma verb lived in a tray carrying eighteen items of
supervisor vocabulary.

**App menu.** Lemma (About, Settings… ⌘,, Connection…, Services, Hide, Quit and
Stop Lemma ⇧⌘Q, Quit ⌘Q), Edit, View (Lemma Home, Back ⌘[, Forward ⌘],
Reload ⌘R, Fullscreen, Developer Tools ⌥⌘I), Window, Help (Docs, Diagnostics…,
Open Logs, then Start / Restart / Stop / Stop Lemma completely). Operational
verbs live under Help because starting and stopping services is what you do when
something is wrong, not part of using Lemma.

**Tray.** A status line — "Lemma: running", the stack's own glanceable state,
which only the Agent Host previously had — then Open Lemma, Log In…, Local
settings…, the Agent Host toggle, a **Troubleshoot** submenu holding everything
operational, and Quit.

**One Quit.** *Quit and Stop Lemma* originally sat beside *Quit* in both the app
menu and the tray, which made it read as the tidy way to leave — so people chose
it, released the VM's memory, ended their schedules, and then paid a 20-second
cold start next time. Quitting and stopping the server are different intentions.
Quit is now the only quit; the server controls live together under
**Troubleshoot**, as **Stop the local server** and **Stop the local server and
quit** for someone who genuinely wants the machine's resources back.

**Names.** *Stop Services and Infra* → **Stop Lemma completely**. *Switch
Connection Mode* → **Connection…**. *Start Services* → **Start Lemma**.
`the_menu_bar_speaks_the_products_language` keeps the retired vocabulary out.

Both surfaces route through one `handle_menu_action`, which is what stops them
drifting into two products' worth of behaviour. Items that need a local stack
are disabled in hosted mode rather than failing when pressed.

---

## 6. The keychain modal

Chatting with a local agent put up a macOS panel: *A keychain cannot be found to
store "secret-encryption-keyset"*, with a **Reset To Defaults** button that
failed the same way. Nothing worked past it.

Two decisions that are individually right were fatal together. The packaged host
pack sets `SECRET_KEY_PROVIDER=keychain` so encryption material lives in the OS
vault rather than on disk. It also sets `HOME` to an app-owned directory, so a
packaged service neither depends on nor mutates the user's home. But macOS
resolves the login keychain out of `$HOME/Library/Keychains` — so the backend
looked for a keychain in a directory that has none, and the Security framework's
answer to that is a modal, not an error code.

locald has no such problem: it runs as an ordinary process of the signed-in user
and already keeps `ai.api_key` in the vault. `backend_environment()` now fetches
— minting once on first use — a Fernet keyset from that vault and hands it to
the backend as `SECRET_ENCRYPTION_KEYSET` with `SECRET_KEY_PROVIDER=static`.
Operator environment is applied over the manifest's, so this is what decides.
The material still lives in the OS vault exactly as intended; the backend simply
stops reaching for it.

One migration note: an installation that somehow *did* hold a keychain-minted
keyset has rows encrypted under it and will now be handed a different one. In
practice the keychain provider could not succeed in a packaged install — that is
the bug — so there should be nothing to migrate; a packaged install that
predates this and has encrypted secrets would need its old keyset copied into
`secret.encryption_keyset` in the vault.

---


## 7. The agent that never answered

Chat with a local coding agent sat at "Thinking" and eventually produced
*"Agent Host reached terminal checkpoint FAILED without its required terminal
event"*. The Agent Host log said what actually happened:

```text
WARN  publishing probed harnesses failed
      error=… error sending request for url (…/agent-host/harnesses)
WARN  no harnesses published yet; the command will be rejected
ERROR Agent Host command failed  error=command references an unknown harness
```

The Agent Host probes the agents installed on this Mac and publishes them to
the backend; a run command names one of those published harnesses. The
scheduling was:

```rust
if self.refresh_due <= Instant::now() {
    self.refresh_harnesses();                                  // spawned, outcome unread
    self.refresh_due = Instant::now() + HARNESS_REFRESH_INTERVAL;  // 15 minutes
}
```

`refresh_due` was pushed out a full fifteen minutes *before* anyone knew whether
the publish had worked, and a failure was only logged. Fifteen minutes is a
reasonable answer to "have the installed agents changed"; it is the wrong answer
to "that request failed". The backend restarts whenever its configuration
changes — applying an AI provider does exactly that — so a publish landing in
that window left the host with nothing published, and every command for the next
quarter of an hour was rejected for naming a harness it had never announced.

Three changes:

- **The publish outcome reaches the loop.** The probe channel carries an
  outcome rather than only successes, and a failure schedules a retry ten
  seconds out instead of fifteen minutes.
- **A command asks rather than only waiting.** Reaching `await_first_harnesses`
  means something needs a harness we have not published — which is the moment to
  publish, not to sit out the remaining interval and then reject.
- **A retry inside the wait.** A failure arriving while a command is waiting
  retries within the deadline already being held.

Worth noting what this was not: the endpoint was reachable the whole time
(`200` in 8 ms when checked directly). The failures were transient restarts, and
the bug was entirely in how a transient failure was rescheduled.

---

## 8. Failures that named nothing and suggested nothing

Two reports, one shape: a knowable cause reported as an opaque one.

**A signed-out agent.** Chatting produced, verbatim:

```text
Internal error: Failed to authenticate: OAuth session expired and could not be
refreshed: {"errorKind": "authentication_failed"}
```

That is Claude Code's own internal error, forwarded untouched. It is accurate
and useless: it names no agent, suggests nothing to do, and reads like a defect
in Lemma rather than a session that needs renewing. `authentication_hint`
recognises the handful of ways an adapter says "not signed in" and replaces the
text with the agent's name and the fix. Anything it does not recognise still
travels verbatim — guessing would bury the one line that explains an unfamiliar
failure, which is why the classifier returns an `Option` rather than a default.

The same classifier runs on the *probe*, so a signed-out agent is now published
as `AUTH_REQUIRED` rather than `PROBE_FAILED`. The workspace already had the
right copy for that state — "Sign-in needed", with the fix — and had simply
never been told, because every probe failure looked alike.

**An undetectable agent.** OpenCode reported `adapter executable opencode was
not found` on a machine that had it at `~/.opencode/bin/opencode`.

Detection was not broken in general — it searches a list of well-known
directories precisely because a GUI-launched app inherits
`/usr/bin:/bin:/usr/sbin:/sbin` and never a login shell's `PATH`. The list just
did not know where OpenCode puts itself. Most of its entries are toolchain and
package-manager `bin` directories, where an agent lands when installed *by*
something; what was missing is the other kind — the directories these agents'
own installers create, which no package manager knows about. `~/.opencode/bin`,
`~/.claude/local`, and `~/.codex/bin` are now searched too.

The list is extracted into a pure function so a test can assert its contents
without mutating global environment. On a machine where nothing is on `PATH` —
which is every GUI launch — that list *is* detection, so the cost of an omission
is an agent the user can see installed and Lemma insists does not exist.

---

## Still open

- **Reproducing the original black screen.** §2 fixes both credible causes, and
  the 120-second blocking quit is the one that matches the report, but the
  diagnosis came from reading the code rather than from a reproduction. Worth
  confirming on a real build which path the user was hitting.
- **Making a configured coding agent the default for new chats.** The banner no
  longer nags once one exists, but `system_default_runtime_config()` still
  resolves to the operator-config provider, so a chat started with no explicit
  runtime will not pick the agent up. That is a backend change with scoping to
  think through: system defaults are global, harness profiles are not.
- **Bundling Inter** for the control page, closing the last §5a divergence.
- **Moving Local settings into the pod shell** behind an audited bridge (§5a
  option B), if the security model is worth re-opening for it.
- **Public link in onboarding** currently presents the option and hands off to
  Local settings rather than running an ngrok or Cloudflare setup inline. That
  is the conservative reading of "ask about sharing"; running the tunnel setup
  inside the flow is a larger piece.
