# Lemma Desktop — production-readiness audit

Date: 2026-08-14 · Scope: macOS local mode (Desktop 0.7.0)

**Status: P0-1 through P0-5, P1-1 through P1-7 and P1-9 are fixed in the PR this
document arrived with.** They are left written in the present tense because the
finding is the useful part — each says what was wrong and how it was reached,
which is what a reader needs when a symptom looks familiar again. Still open:
**P0-6** (no auto-updater) and the `.pkg` question, both deliberately deferred;
**P1-8** (publish retry cadence), now instrumented rather than fixed; and every
P2. The three findings marked **[needs repro]** were fixed defensively — they
were diagnosis from reading, and the instrumentation added alongside is what
will confirm or refute them from the next nightly's log.

## Where this came from

Three sources, kept separate so a claim can be traced back:

1. **Roundtable board** — pod `roundtable` (`019f1ebd-f531-73f8-a8f2-3829eca62409`), org
   `019ddd81-…be0d` (deepakjha0196), server `lemma-cloud`. 89 issues; 25 read in full with
   comments and attachments; 8 screenshots viewed. Desktop-relevant: RT-92, 102, 104, 121,
   133, 135–147.
2. **Ayush's Agent Host log** (12–13 Aug), pasted in the request.
3. **Code read** of `desktop/` (44 Rust files, ~40k lines), the Agent Host surfaces in
   `lemma-frontend/`, and the Agent Host module in `lemma-backend/`.

Findings below that carry a `file:line` are read off the code. Findings marked
**[needs repro]** are diagnosis from reading and should be confirmed on a build before
someone spends a day on the wrong fix.

---

## Verdict

The plumbing is good. Install, signing, notarization, artifact verification, supervision,
the journal, the lease/heartbeat protocol, cancellation, and the permission gate are all
built to a standard that holds up under adversarial reading. **What is not production ready
is the first hour.** Every serious problem below is in one of three seams:

- **the harness lifecycle** — detect → publish → bind a profile → run. Its state churns for
  reasons the user never sees, and each churn turns into a permanently failed run with a
  sentence nobody can act on.
- **the honesty of status** — the app knows more than it says. It knows an agent is signed
  out at probe time and shows it as available. It knows it is still scanning and shows a
  panel promising three agents.
- **the default path** — a desktop app whose first screen recommends the website.

Ship-blocking count: **6 P0**, **9 P1**.

---

## P0 — blocks a production desktop app

### P0-1 · A queued run dies permanently when the host re-publishes its harnesses

This is the root cause of Ayush's log and of RT-142.

`config_revision` is `sha256(adapter_version, upstream_version, config_options,
capabilities)` — [runtime.rs:1858](desktop/agent-host/src/runtime.rs:1858). The backend
stamps a START_RUN with whatever revision the DB holds *at enqueue*
([agent_host_dispatch.py:143,185](lemma-backend/app/modules/agent/infrastructure/harnesses/agent_host_dispatch.py:143)).
The host compares it against its own current view when the command lands
([runtime.rs:631](desktop/agent-host/src/runtime.rs:631)):

```rust
anyhow::ensure!(
    published.config_revision == spec.profile_revision,
    "harness configuration revision changed"
);
```

Any re-publish between those two moments loses the run. That is not rare:

- the 15-minute refresh ([runtime.rs:31](desktop/agent-host/src/runtime.rs:31)) re-probes on
  a timer;
- **Claude Code auto-updates itself**, and `upstream_version` is in the hash — every
  upgrade changes the revision;
- Claude Code's model list changes between releases, and `config_options` is in the hash;
- a publish that fails and later succeeds lands a new revision on commands minted during
  the outage. This is exactly Ayush's trace: seven minutes of
  `publishing probed harnesses failed` (08:23–08:30), then three commands rejected inside
  45 ms at 08:31:01.

The rejection is classified **non-retryable**
([runtime.rs:1502](desktop/agent-host/src/runtime.rs:1502)), and the backend then marks the
lease `FAILED` and terminal
([agent_host_control_updates.py:191](lemma-backend/app/modules/agent/infrastructure/agent_host_control_updates.py:191)).
The user sees a dead run and the words "harness configuration revision changed".

`docs/design/local-desktop-app-experience.md` §7 fixed the sibling of this bug
(`unknown harness`) and stopped one step short. Both mean the same thing — *the host has
re-published since this command was minted* — and both are recoverable.

**Fix.** Make `CONFIG_REVISION_STALE` retryable, and have the backend re-mint the START_RUN
against the fresh revision (re-validating `config_selections` and `model_name` against the
new `config_options`) instead of terminalizing. Bound it — two re-mints, then fail with a
message that names the agent and says it changed under the run. Secondary: drop
`upstream_version` from the revision hash unless `config_options` also changed; a patch
release of Claude Code that offers the same models is not a configuration change.

---

### P0-2 · `session/load` can strip the model list, and the run dies on it

RT-141. Screenshot: `Invalid params: "selected model is not offered by this harness"` on a
model the picker itself offered (`opus[1m]`).

The model check runs against `safe_options`, which on a resumed conversation comes from
`session/load`'s response, not from the probe
([acp.rs:297–312](desktop/agent-host/src/acp.rs:297)):

```rust
let (session_id, config_options) = if let Some(established) = established {
    established                                   // session/load — configOptions is OPTIONAL in ACP
} else {
    …NewSessionRequest…                           // session/new — what the probe published
};
```

Then ([acp.rs:340–352](desktop/agent-host/src/acp.rs:340)) a missing model option is a hard
error (`this harness does not expose model selection`) and a model absent from a *reduced*
list is a hard error (`selected model is not offered by this harness`). Every Lemma
conversation after the first message resumes, so this fires on turn 2+, not turn 1 —
consistent with the screenshot, where the first prompt got through and `go ahead` did not.
**[needs repro]** — confirm by logging `config_options` on both branches for `claude-code`.

The same asymmetry exists in the probe itself: the probe opens a session with **no MCP
servers** ([acp.rs:114](desktop/agent-host/src/acp.rs:114)) while a run passes them
([acp.rs:305](desktop/agent-host/src/acp.rs:305)). Two different sessions, one published
answer.

**Fix.** Treat the published options as the contract. If `session/load` returns no or fewer
config options, fall back to the probed set rather than failing; if the model genuinely is
not offered, do not kill the run — fall back to the harness default and emit a visible
status ("Claude Code no longer offers `opus[1m]`; used its default"). A model name is not
worth losing a turn over.

Related, same issue: raw ids leak into the UI. `opus[1m]`, `claude-fable-5[1m]` are shown to
users verbatim because the catalog takes `item.value ?? item.id` for both `name` and
`display_name` when no `name` is present
([runtime_profile_service.py:733–748](lemma-backend/app/modules/agent/services/runtime_profile_service.py:733)).

---

### P0-3 · An agent that is signed out is shown as ready to use

RT-140. The user added Claude Code, chatted, and only then learned it was not signed in —
and after `claude /login` it *still* failed until they restarted the Agent Host from Local
settings.

Three separate defects behind one report:

**(a) Onboarding renders no health at all.** The harness row in
[local-setup-steps.tsx:283–344](lemma-frontend/components/onboarding/local-setup-steps.tsx:283)
prints `harness.display_name` and an "Use in chats" button. Nothing else. `health` and
`stale_reason` are on the wire and the vocabulary already exists —
`AUTH_REQUIRED → "Sign-in needed"`
([agent-runtime-helpers.ts:235](lemma-frontend/components/agents/agent-runtime-helpers.ts:235))
— and *Manage models* uses it
([models-settings.tsx:883,924](lemma-frontend/components/agents/models-settings.tsx:883)).
Onboarding, where a new user actually is, does not. So a signed-out agent is offered for
adoption exactly like a working one.

**(b) The probe probably cannot see the sign-out.** §8 of the design doc claims the
classifier runs on the probe, and it does
([runtime.rs:1058](desktop/agent-host/src/runtime.rs:1058)) — but only if `session/new`
fails. Ayush's evidence says it did not: the harness listed fine, and the auth error
appeared on `session/prompt`. **[needs repro]** — probe `claude-agent-acp` with an expired
session and record which request fails. If `session/new` succeeds, the probe needs a
cheap authenticated call before it may publish `READY`.

**(c) After signing in, there is no way to say so.** `AUTH_REQUIRED` blocks admission
([agent_host_admission.py:164](lemma-backend/app/modules/agent/infrastructure/agent_host_admission.py:164)),
and health only refreshes on the 15-minute timer. The chat error says *"sign in, and send
the message again"* ([runtime.rs:1909](desktop/agent-host/src/runtime.rs:1909)) — which is
wrong, because sending again does nothing for up to fifteen minutes. That is precisely why
Ayush had to restart the host. A re-probe path exists (`agentHostBridge.refresh()` → the
`refresh_generation` bump at [runtime.rs:541](desktop/agent-host/src/runtime.rs:541)) — the
error just never offers it.

**Fix.** Health badge + `stale_reason` on the onboarding rows, same component as Manage
models. Withhold "Use in chats" for non-READY, as Manage models already does. Put a
**"I've signed in — re-check"** button directly on the chat error, wired to the existing
refresh, and make an `AUTH_REQUIRED` harness re-probe on demand rather than on a timer.

---

### P0-4 · The Lemma MCP bridge dies on any single non-2xx, mid-run

[mcp_bridge.rs:104](desktop/agent-host/src/mcp_bridge.rs:104):

```rust
if !response.status().is_success() {
    anyhow::bail!("Lemma MCP endpoint returned HTTP {}", response.status());
}
```

That returns from `run_bridge`, which is the whole process
([main.rs:436](desktop/agent-host/src/main.rs:436)). One 502 during a deploy, one 500, one
401 arriving a beat before `REFRESH_CREDENTIAL` lands — and the agent's Lemma MCP server
vanishes for the rest of the run. The in-flight JSON-RPC call gets no error response, just
a closed stdio pipe, so the agent cannot even report what happened. The design doc itself
notes that *"the backend restarts whenever its configuration changes"*, so this is not
hypothetical.

This is a strong candidate for RT-142 ("inconsistent runtime behaviour") and for RT-90
("how do I know which tool is failing").

**Fix.** Retry idempotent MCP requests with backoff (5xx and 429), re-read the journal
credential and retry once on 401, and on final failure return a JSON-RPC error object for
that request id and keep the bridge alive. The bridge should only exit when stdin closes.

---

### P0-5 · The desktop app's first screen recommends the website

RT-137: *"installed dmg and directly saw the landing page. Had to manually go to
Connections on top bar and select local."*

The first-launch chooser exists ([ui/index.html:684–702](desktop/ui/index.html:684)), and it
is weighted against the reason the app was downloaded:

| | Cloud | Local |
|---|---|---|
| Class | `choice recommended` + **Recommended** badge | `choice` |
| Position | first | second |
| Call to action | `Continue →` | `Review →` |
| Body | "Ready immediately." | "Set up local services and keep workspace data here." |

Someone who downloads a 100 MB desktop app to run Lemma on their Mac is steered, by the
app's own recommendation, into a browser view of lemma.work. "Review →" reads as homework.

**Fix.** In the desktop app, local is the default and the recommendation. Keep cloud as an
equal, unbadged second option ("Already have a workspace? Sign in to lemma.work"). Give
local a `Continue →` too and keep the disclosure screen behind it — the disclosure is right,
the label is what makes it feel like a detour.

---

### P0-6 · There is no update mechanism

No `updater` in `bundle.targets` ([tauri.conf.json](desktop/tauri.conf.json)), no
`tauri-plugin-updater` in [Cargo.toml](desktop/Cargo.toml), no `latest.json` in
[release-desktop.yml](.github/workflows/release-desktop.yml). Every fix in this document
reaches a user only if they notice, revisit the release page, and re-download.

For an app that supervises a local stack, this is worse than for an ordinary app: the shell
version gates the resume target
(`docs/design/local-desktop-app-experience.md` §1), so a stale shell and a fresh host pack
disagree in ways the user experiences as a blank window.

**Fix.** Ship `tauri-plugin-updater` with a signed `latest.json` on the release. Silent
download, prompt on next launch. Same signing identity already in CI.

---

## P1 — a user hits these in the first session

### P1-1 · The detection panel says two contradictory things at once
RT-139, screenshot confirmed. Left: *"Still looking for coding agents on this Mac…"*.
Right, at the same moment: *"Two ways to get a working agent — Claude Code, Codex, or
OpenCode."* The four honest states are computed
([local-setup-steps.tsx:351–363](lemma-frontend/components/onboarding/local-setup-steps.tsx:351))
but the preview panel is a static prop
([:244–253](lemma-frontend/components/onboarding/local-setup-steps.tsx:244)).
**Fix (Deepak's own suggestion, and it is right):** prepopulate the four known agents as
skeleton rows with a shimmer, then resolve each to ✅ *available* / ⚠️ *sign-in needed* /
grey *not installed*. The panel then narrates one thing.

### P1-2 · No progress while probing
Probing spawns each agent, runs `initialize` + `session/new`, and waits up to 20 s each
([runtime.rs:1040](desktop/agent-host/src/runtime.rs:1040)); a cold start with a timing-out
adapter took 47 s to first heartbeat by the design doc's own measurement. During that the
UI shows one static sentence. Per-agent progress is available and unused.

### P1-3 · `AUTH_REQUIRED` has no action anywhere
The copy is *"Sign in to this agent on that computer, then let Agent Host re-probe"*
([agent-runtime-helpers.ts:236](lemma-frontend/components/agents/agent-runtime-helpers.ts:236)).
There is no button that re-probes. See P0-3(c).

### P1-4 · Workspace sandbox image is pulled lazily, silently, from the network
`core.images` prepares Postgres/Redis/SuperTokens at boot
([managed_runtime.rs:554](desktop/locald/src/managed_runtime.rs:554)). The **workspace and
function sandbox images are not**: they are pulled on first `sandbox.ensure`
([guestd/lib.rs:1462](desktop/local-runtime/guestd/src/lib.rs:1462)) with `pull --quiet`
([:1533](desktop/local-runtime/guestd/src/lib.rs:1533)) — no progress, inside the VM, from
ghcr.io. So the first time a pod actually does work, the user waits on an unreported
multi-hundred-MB download; on a flaky connection they get
`guest_cache_repair_required` and an automatic VM restart.
**Fix.** Pre-pull both sandbox images during local setup, on the same progress bar as the
rest, and report pull bytes. Fail setup loudly rather than the first run silently.

### P1-5 · Local coding agents run in an empty scratch directory, and nothing says so
An ACP run's cwd is `…/scratch/<target>/<conversation>`
([runtime.rs:1735](desktop/agent-host/src/runtime.rs:1735)) — not the pod workspace, not a
repo, not the user's project. It reaches pod content only through Lemma MCP tools. That is
a defensible design, but it is invisible: RT-141's screenshot is a user asking *"we want to
build this on lemma (but locally) it should run on my mac"* against an empty directory.
**Fix.** Say what the agent can see, once, in the composer or the first turn. And decide
deliberately whether "local agent + local pod" should mount the pod workspace — right now
the two local things do not meet.

### P1-6 · Model choice is validated three times against three different snapshots
Profile save validates against the published `config_options`
([agent_host.py:449](lemma-backend/app/modules/agent/domain/agent_host.py:449)); admission
re-validates against the live DB row
([agent_host_admission.py:170](lemma-backend/app/modules/agent/infrastructure/agent_host_admission.py:170));
the host validates against the live ACP session
([acp.rs:346](desktop/agent-host/src/acp.rs:346)). Three chances to disagree, and the third
one is fatal to the run. RT-141 case 2 (default `opus` in Manage models, then `opus` in
chat) lives here. Collapse to one authority — the published snapshot — and make the third
check advisory.

### P1-7 · Two revision functions that hash different things
[adapters.rs:431](desktop/agent-host/src/adapters.rs:431) hashes
`{adapter, upstream, config}`; [runtime.rs:1859](desktop/agent-host/src/runtime.rs:1859)
hashes `{adapter_version, upstream_version, config_options, capabilities}`. Same concept,
different keys, so the same harness state hashes differently depending on which path
produced it. Also, the auth/probe-failure branches
([runtime.rs:1051–1069](desktop/agent-host/src/runtime.rs:1051)) mutate `health` and
`stale_reason` without recomputing the revision, so the revision does not track health.
One function, one input struct.

### P1-8 · Publish retry does not behave like 10 seconds
`HARNESS_RETRY_INTERVAL` is 10 s
([runtime.rs:40](desktop/agent-host/src/runtime.rs:40)) but Ayush's warnings are exactly
60 s apart (08:24:09 → 08:25:09 → 08:26:09 → …). The retry deadline is set on the loop
([runtime.rs:440](desktop/agent-host/src/runtime.rs:440)) but the loop then blocks in a 25 s
long poll plus `poll_after_ms`, so `refresh_due <= now` is only tested when the poll
returns. **[needs repro]** — worth a test that pins observed retry cadence, not just the
constant. Seven minutes of failed publishes is seven minutes of runs that cannot start.

### P1-9 · Raw seconds in the Agent Host card
`Ready for work · 1434s uptime`
([control.js:513–514](desktop/ui/control.js:513)) — screenshot confirmed on RT-140. The
web card next door already has `formatUptime`
([this-computer-card.tsx:40](lemma-frontend/components/agents/this-computer-card.tsx:40)).

---

## P2 — polish, cheap, visible

| # | Issue | Where |
|---|---|---|
| P2-1 | `✓ Ready` sits proud of the Continue button (RT-138, screenshot) | [local-setup-steps.tsx:463–478](lemma-frontend/components/onboarding/local-setup-steps.tsx:463) |
| P2-2 | Muted suggestion text cropped in the chat input (RT-133) | new-pod home composer |
| P2-3 | No way back to the Codex/Claude build prompt after onboarding (RT-135) | onboarding → post-setup surface |
| P2-4 | Two error blocks for one failure: raw adapter text *and* the friendly card (RT-140 screenshot) | run-message writer; suppress the raw one when `authentication_hint` fired |
| P2-5 | Local settings still asks for Inter and falls back to the system face | design doc §5a, still open |
| P2-6 | "Which tool failed" is unanswerable from the error (RT-90) | tool-failure message needs the tool name |

---

## Already fixed on `main` — do not re-file

Verified against git history; these appear on the board but are landed:

- **RT-136** chat auto-scrolling to the first message → `41818ffb` (14 Aug), scroll-intent
  rewrite. Confirm in the next nightly.
- **Lemma tools arriving under the wrong name** (`mcp__lemma__…` vs `mcp__lemma_tools__…`),
  **"Always allow" asking every turn**, **approval cards outliving their run**, **a pod
  created in onboarding with no model** → `b1b779bc` (12 Aug). Ayush's report straddles this
  merge, so part of what he saw is already gone.
- **DMG notarized and stapled, not just the app inside it** → `681dc21c`.
- Sandbox process supervision, run failure reporting, grant semantics → `511e5bd7`.

Things the adversarial read found **correct**, worth recording so nobody re-opens them: the
50-minute run deadline genuinely exceeds the 30-minute permission window
([agent_host_run_window.py:43](lemma-backend/app/modules/agent/infrastructure/harnesses/agent_host_run_window.py:43)),
so a parked approval cannot be killed by its own run; artifact install is sha256-verified
with size and entry caps; the permission gate's generation tagging correctly survives the
session-id key collision; policy-bearing options are filtered on both sides with the
deny-list checked before membership.

---

## Installer: should we ship a `.pkg`?

**Tauri v2 cannot build one.** `BundleType` is `Deb | Rpm | AppImage | Msi | Nsis | App |
Dmg` — there is no `pkg` variant (confirmed against `tauri-utils/src/config.rs` for v2;
we pin CLI 2.11.4). A `.pkg` means adding a post-`tauri bundle --bundles app` step:
`pkgbuild` → `productbuild` → `productsign` with the **Developer ID Installer** certificate
(a different cert from the Developer ID Application one CI already loads) → `notarytool` →
`stapler`.

**What a `.pkg` would actually buy us**, given what this app does:

- **Installs to `/Applications` without a drag.** Removes the whole class of "user ran Lemma
  from the DMG / from `~/Downloads`" bugs. Real: this app writes a runtime tree, spawns
  sidecars, and records resume targets keyed to its own version.
- **Preinstall/postinstall scripts.** A postinstall could pre-pull the sandbox images
  (P1-4), pre-place the host pack, and register `lemma-stack` — turning the current
  multi-minute first-run into work done while the installer bar moves.
- **Upgrade semantics.** A `.pkg` can stop the old locald cleanly before replacing the app.
  Today, installing over a running install is a known hazard the resume logic had to be
  hardened against.
- **MDM.** Managed Macs deploy `.pkg`, not `.dmg`. `install.sh` already has a
  `--cli-only` path "for managed macOS users", which is an admission that we have no
  managed install story.

**What it costs:** a second Apple certificate in CI, a hand-written component plist
(sidecars have their own signatures and identifiers that `pkgbuild` must not disturb —
`work.lemma.locald` is asserted by name in the release workflow), and losing the
drag-to-Applications affordance people know.

**Recommendation.** Ship **both**, `.pkg` as the primary download. The DMG stays for people
who expect it and for the nightly channel. Sequence it *after* P0-6 (updater), because an
updater removes most of the reason anyone re-runs an installer at all — and do not let the
`.pkg` work delay the updater. Gate on: does postinstall pre-pulling the sandbox images
actually move first-run time? If not, the `.pkg` is mostly about MDM and `/Applications`,
which is still worth it but not urgent.

---

## Suggested order

1. **P0-1** and **P0-4** — the two that silently kill work in progress. Both are contained
   changes with clear tests.
2. **P0-3** — three small changes, one whole class of "it says it works and it doesn't".
3. **P0-2** — needs the repro first; do not guess at it.
4. **P0-5** — an afternoon, and it changes what every new user experiences.
5. **P1-1/P1-2** — the detection panel, as one piece of work.
6. **P0-6** — the updater, which is what makes everything above reach anyone.
7. **P1-4** — pre-pull, ideally landing with the `.pkg` postinstall.

## Open questions for the team

- P0-2, P0-3(b), P1-8 each need one reproduction before someone commits to a fix.
- Should a local coding agent see the local pod's workspace (P1-5)? That is a product
  decision, not a bug, and it is the one users keep walking into.
- RT-142 says *"Log sent to Anukul on slack"* — that log is not on the board. Attach it, or
  the finding above is the best reconstruction available.
