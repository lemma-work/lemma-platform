# Desktop security

Lemma Desktop runs a complete Lemma on one person's computer: the backend and
the workspace on the Mac (or Windows host), and PostgreSQL, Redis, SuperTokens
and every sandbox container inside a private guest VM (VZ on macOS, WSL on
Windows). This page is the threat model for that arrangement — who can reach
it, what each of them gets, and which mechanism holds each line. The process
layout itself is in [Desktop architecture](desktop.md).

## The installation owner

The **installation owner** is the first account created on a Desktop
installation. It is the person whose computer this is, and it is the only
account that will be granted anything outside the VM.

- Recorded once, in the one-row `installation_owner` table (identity module,
  migration `0040`). The primary key is a boolean pinned to `true` by a check
  constraint, so a second owner is a statement the database refuses rather
  than a race someone has to lose.
- The slot is **reserved before the account exists**. Two simultaneous first
  signups both try to insert the reservation; one succeeds and proceeds as the
  owner, the other is treated as an ordinary signup and meets the signup mode.
  The reservation is bound to the user in the same transaction that creates the
  user row. A reservation abandoned mid-signup (a rejected password, a closed
  tab) can be taken over after `INSTALLATION_OWNER_RESERVATION_SECONDS`.
- An installation that already had accounts when it was upgraded makes its
  **oldest** account the owner, the first time anything asks.
- A deleted owner leaves the slot *taken* (`ON DELETE SET NULL`). Ownership is
  never handed to whoever signs up next.
- Ownership exists only when `DEPLOYMENT_KIND=desktop`, which the Desktop host
  pack sets. A hosted or self-hosted deployment has no owner; the check does not
  consult the table there at all.

The frontend reads it from `GET /users/me/installation`:

```json
{ "deployment": "desktop", "is_owner": true, "signup_mode": "invite_only" }
```

## Who can reach the installation

Sharing (Local settings → Sharing) decides which networks can reach the
workspace. It is enforced by locald's gateway and, for Public, by the tunnel.

| Mode | Reachable from | Notes |
| --- | --- | --- |
| This computer | This Mac only | Services bind loopback; abuse controls off |
| Local network | The selected private IPv4 interface | HTTP, host-only cookies; meant for trusted Wi-Fi |
| Public | The internet, through the owner's ngrok or Cloudflare account | HTTPS at the tunnel, secure cookies |

Leaving This computer applies an environment overlay to the backend
(`sharing_environment()` in `desktop/locald/src/daemon/environment.rs`): it
rewrites every URL to the shared origin, turns the auth abuse controls and
ALTCHA back on, caps desktop-auth handoffs, switches `DEBUG` off, and sets
`SIGNUP_MODE` from the sharing preference below. Email verification stays off
— a Desktop installation has no mail transport by default.

Published pod apps stay local-only in every mode: their routing needs wildcard
subdomains, which a tunnel does not provide.

## Who can create an account

`SIGNUP_MODE` is enforced by the identity module on every path that creates a
user — the email/password sign-up API, the OAuth sign-in-up recipe, and the
email-code completion used by browser email sign-in and chat onboarding
(`SignupGate.admit`):

| Mode | Who may create an account |
| --- | --- |
| `open` | Anyone who reaches the sign-up page |
| `invite_only` | Only an address with a pending, unexpired organization invitation |
| `closed` | Nobody |

On Desktop the first account is admitted whatever the mode — there is nobody
yet who could have invited it. People who already have an account sign in
regardless of the mode. A refusal reaches the auth screen as a sentence, not a
status: *"This Lemma is invite-only. Ask its owner for an invitation."*
(`SIGNUP_INVITE_ONLY`), or *"This Lemma is not accepting new accounts."*
(`SIGNUP_CLOSED`).

Defaults: `open` for hosted and self-hosted deployments (the behaviour before
the setting existed), `invite_only` for Desktop. Sharing carries its own
preference, **Who can join** (`who_can_join` in `sharing.json`: `invite_only` by
default, or `open`). It is part of the control snapshot, it can be changed while
sharing is live (`sharing.access`, which restarts only the backend), and the
Public confirmation and LAN warning on the Sharing page describe whichever is
in force.

## What a non-owner gets

An invited member — or anyone at all, if the owner chose `open` — gets what a
member of any Lemma gets: a personal workspace, pods they are invited to, and
**a sandbox container inside the owner's VM**. That container is the boundary,
and it is hardened accordingly:

- **No capabilities.** `--cap-drop ALL` and `--security-opt no-new-privileges`
  on every sandbox (`build_run_arguments` in
  `desktop/local-runtime/guestd/src/sandbox_run.rs`). Both sandbox images run
  as an unprivileged user; Chrome runs `--no-sandbox` because the container is
  its sandbox.
- **No reach into the guest's own services.** PostgreSQL, Redis and SuperTokens
  run with host networking inside the VM. guestd installs a
  `LEMMA-SANDBOX-ISOLATION` chain that rejects new TCP connections arriving from
  the sandbox bridge (`nerdctl0`) on ports 5432, 6379 and 3567, before it
  starts any sandbox, and refuses to start one if the rules cannot be installed
  (`sandbox_firewall.rs`). Internet access, the backend's connections into a
  sandbox (published ports) and callbacks to the host are all unaffected.
- **Nothing on the host.** Non-owners never get host command execution.

A function sandbox is additionally read-only with a `noexec` `/tmp`.

## What the owner will get

The next change adds command execution on the host itself — outside the VM —
for the installation owner only, gated on `is_installation_owner`. This page
will describe that boundary when it exists; everything above is the
prerequisite for it being safe to add.

## The host alias

Every sandbox can reach the host through `host.lemma.internal`, which guestd
adds to the container's hosts file pointing at the VM's host gateway. The
backend and workspace forwarders listen there, and the workspace runtime's
callbacks and the function gateway use it — so today every sandbox needs it.

It is now an explicit per-sandbox flag, `host_access` on `sandbox.ensure`
(`ProviderCreateSpec.host_access` in the backend, passed through the bridge
and hostctl unchanged). It defaults to `true`, and the backend sends it only
when it is `false`, so a guest that predates the flag keeps working. A later
change can restrict it to the owner's own browser sandbox.

The flag controls a name, not a route: without the alias a container can still
dial the gateway by address. Closing that needs a per-container firewall rule,
which is what the flag is there to key.

## The Tauri IPC origin rule

The workspace page is a remote origin to Tauri, and reaches the shell only
through a capability that names this Mac's own local origin. A shared origin —
the LAN address or the tunnel host — is deliberately absent from that
capability and fails the Rust-side caller check too. A visitor's browser can
drive the shared Lemma; it can never invoke the desktop shell, the Agent Host
or anything that touches the local stack. See
[The privilege boundary](agent-host.md#the-privilege-boundary).

## What is not covered

- **Sandbox-to-sandbox traffic.** Sandboxes share one bridge. A sandbox can
  reach another's published ports through the guest; those ports require the
  per-sandbox runtime credential, but the network does not separate them.
- **IPv6.** The isolation rules are IPv4; nerdctl's default bridge is IPv4-only.
- **Containers created before an upgrade** keep the arguments they were
  created with until the next time `sandbox.ensure` replaces them.
