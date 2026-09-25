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
  migration `0041`). The primary key is a boolean pinned to `true` by a check
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

Sharing (Settings → This Mac → Sharing) decides which networks can reach the
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
in force. Enabling Public from the workspace is confirmed in a native dialog
the shell raises itself (`local_sharing`); the page cannot set the consent
flag.

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
adds to the container's hosts file pointing at the VM's host gateway. locald
runs two callback forwarders there — the backend's and the frontend's ports,
relayed to the Mac's loopback — and the workspace runtime's callbacks and the
function gateway use them, so every sandbox, the owner's and an invited
person's alike, needs the alias.

It is an explicit per-sandbox flag, `host_access` on `sandbox.ensure`
(`ProviderCreateSpec.host_access` in the backend, passed through the bridge
and hostctl unchanged). It defaults to `true`, and the backend sends it only
when it is `false`, so a guest that predates the flag keeps working.

The alias is **not** a way onto the Mac's own loopback: a server on the Mac's
`127.0.0.1` is not reachable at the gateway address. That is the loopback
relay, below, and only the owner's sandbox has it.

The flag controls a name, not a route. What a sandbox can reach at the gateway
address is decided by guestd's firewall, below.

## What a sandbox can reach on the Mac

The host gateway is the Mac's own address on the VM's network, so without a
rule a container could dial any Mac service listening on every interface. guestd
installs one, in `sandbox_firewall.rs`, before it starts any sandbox:
traffic from the sandbox bridge (`nerdctl0`) to the gateway passes a chain
(`LEMMA-HOST-<hash>`, jumped to from the top of `FORWARD`) that lets through
replies to connections the Mac opened, the two callback ports, and DNS, which
vmnet serves on the gateway, and rejects everything else (`tcp-reset` for TCP,
so a refused connection fails at once rather than timing out).

- **The ports come from locald.** It names the backend's and frontend's ports
  as `callback_ports` in every `core.*` request, and guestd keeps them in
  `run/callback-ports.json` so a restarted guestd still knows them. A guest
  that has never been told refuses to start a sandbox rather than start one
  that can reach nothing it needs.
- **The same rule for every sandbox**, the owner's and an invited person's
  alike. The owner reaches the Mac's loopback through the relay socket, never
  through the gateway, so the owner's sandbox needs no wider rule. Because the
  rule does not vary by container it is keyed on the bridge rather than on
  each container's address.
- **Replaced without a gap.** The chain is named after its contents. New ports
  are built into a new chain in full, jumped to, and only then is the old jump
  and chain removed — the old one must go, because the new chain *returns*
  allowed traffic to `FORWARD`, where the old chain would reject it.
- **Fails closed.** A guest where the rules cannot be installed starts no
  sandbox.

**What it does not cover.** The rule names the gateway address. Another of the
Mac's addresses, such as its LAN address, is reached through the VM's NAT like
any other machine on the network, and a Mac service listening on every
interface answers there. The core ports inside the guest are a separate rule,
`LEMMA-SANDBOX-ISOLATION`.

## The loopback relay

With [host execution](desktop-host-execution.md) the owner's agent starts
`npm run dev` on the Mac, where it listens on `127.0.0.1:3000`, and checks the
result with a browser that runs in the owner's workspace sandbox in the guest.
The loopback relay is how that browser reaches the Mac's loopback:

```
Chrome ─proxy─► host_fallback ─unix─► guestd ─vsock 42413─► lemma-vz ─unix─► locald ─tcp─► 127.0.0.1:<port>
(owner's workspace sandbox)      (relay.sock)                (HostLoopbackBridge)  (loopback_relay)
```

- **Per request, not per port.** Chrome in the owner's sandbox is pointed at
  `sandbox_runtime.host_fallback` (`--proxy-server` plus
  `--proxy-bypass-list=<-loopback>`; a PAC is ignored for loopback). A loopback
  port the sandbox is serving stays the sandbox's, so an agent previewing what
  it built there is unaffected. Only a port nothing in the sandbox answers on
  is asked for through the relay.
- **Only the owner's browser sandbox.** The backend decides, at provision time
  (`host_loopback_policy.is_owner_browser_sandbox`): a workspace, owned by a
  person, on a Desktop install, whose owner is the installation owner. It
  sends `host_loopback: true` on `sandbox.ensure` for that sandbox and no
  other. guestd then bind-mounts its relay directory into that container at
  `/run/lemma-host-loopback` (and refuses the grant for a function sandbox).
  The socket exists only in containers it is mounted into, so there is no
  address an invited person's sandbox could dial. The directory is root's and
  not writable from inside, so the owner's sandbox can use the socket but not
  replace it. This is a separate grant from `host_access`.
- **Only while "Run commands on this Mac" is on.** locald reads the Agent
  Host's `host_execution` setting on every connection, as it does the deny
  list, and admits nothing while it is off: there is then no server of the
  agent's on the Mac to check. Turning it off closes the relay for the next
  request, with no restart.
- **Loopback only, and the port is all that is asked.** A request is digits
  and a newline. guestd refuses anything else, and ports below 1024, before
  opening vsock. locald connects to `127.0.0.1`, then `::1`, on that port —
  never a name and never another address.
- **Not Lemma's own ports.** locald refuses, re-reading the list on every
  connection: the managed runtime's backend, frontend and PostgreSQL, Redis
  and SuperTokens forwards; every loopback health URL in the host pack; the
  sharing gateway and the tunnel's local API (ngrok's inspection port,
  cloudflared's metrics port); and the Agent Host's MCP relay ports, from its
  `mcp-relay/*.json` endpoint files. Privileged ports are refused here too.
  A refusal reaches the sandbox as `error <reason>`, and Chrome shows a failed
  load.
- **Nothing in lemma-vz decides anything.** It carries bytes between guest
  vsock streams and locald's socket (`run/host-loopback.sock`, mode 0600).

**What it does not cover.** While the switch is on, the owner's VM browser —
and any page it loads — can reach any non-Lemma server on the Mac's loopback,
the same exposure the owner's own browser on the Mac has. Every run in the
owner's workspace shares that sandbox, including a run started by an inbound
channel message that resolved to the owner: such a run cannot execute on the
host, but its browser can use the relay while the switch is on. A `curl localhost:3000` in the sandbox's shell does
not go through the relay: the fall-through is Chrome's proxy, not the shell's.

**Windows.** The WSL guest runs guestd per request and never binds the relay
socket, so the owner's sandbox finds no socket, the fall-through is not
started, and `localhost` stays the sandbox's. Before the relay, a loopback miss
was retried on the host alias; that path is gone on every platform.

**Containers created before this** keep the arguments they were created with
until `sandbox.ensure` next replaces them; a guest restart does.

## The Tauri IPC origin rule

The workspace page is a remote origin to Tauri, and reaches the shell only
through a capability that names this Mac's own local origin. A shared origin —
the LAN address or the tunnel host — is deliberately absent from that
capability and fails the Rust-side caller check too. The This Mac settings
commands check more narrowly still: local mode, the loopback workspace origin
this app navigated to, and nothing else — so the owner's own window, once
sharing has moved it to the shared address, is refused as well, and turns
sharing off from the native Local settings instead. A visitor's browser can
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
