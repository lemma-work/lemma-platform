# Desktop security

Lemma Desktop runs a complete Lemma on one person's computer: the backend and
the workspace on the Mac (or Windows host), and PostgreSQL, Redis, SuperTokens
and every sandbox container inside a private guest VM (VZ on macOS, WSL on
Windows). This page is the threat model for that arrangement — who can reach
it, what each of them gets, and which mechanism holds each line. The process
layout itself is in [Desktop architecture](desktop.md).

## Who is the person at this Mac

There is no installation owner, and no account on a Desktop installation is
special. Every account -- the first one included -- is an ordinary member,
exactly as on hosted Lemma. What belongs to *the person at this Mac* is decided
by **where a request comes from**, never by who is signed in:

- **This Mac settings** (sharing, updates, credentials, repair) answer only the
  desktop app's own main window, in local mode, on this installation's loopback
  workspace origin. The shell checks that on every command
  (`require_local_settings_caller` in `desktop/src/workspace_settings.rs`); the
  frontend only mirrors it to decide what to draw (`thisMacAvailability`). A
  browser, a LAN visitor or anyone on the tunnel never reaches the shell, so
  they never see or reach those settings, whatever account they hold.
- **Anything that runs on this Mac outside the VM** follows the **Agent Host
  pairing**. The Agent Host on this Mac is paired to one account, from the
  app's own window; see [Agent Host](agent-host.md).

So two people who both sign in to the Desktop app on this Mac both see This
Mac -- they are both at this Mac. Someone who reaches the same Lemma from a
browser or a phone does not.

## Who can reach the installation

Sharing (Settings → This Mac → Sharing) decides which networks can reach the
workspace. It is enforced by locald's gateway and, for Public, by the tunnel.

| Mode | Reachable from | Notes |
| --- | --- | --- |
| This computer | This Mac only | Services bind loopback; abuse controls off |
| Local network | The selected private IPv4 interface | HTTP, host-only cookies; meant for trusted Wi-Fi |
| Public | The internet, through this Mac's own ngrok or Cloudflare account | HTTPS at the tunnel, secure cookies |

Leaving This computer applies an environment overlay to the backend
(`sharing_environment()` in `desktop/locald/src/daemon/environment.rs`): it
rewrites every URL to the shared origin, turns the auth abuse controls and
ALTCHA back on, caps desktop-auth handoffs, switches `DEBUG` off, sets
`INSTALLATION_SHARED=true`, and sets `SIGNUP_MODE` from the sharing preference
below. Email verification stays off — a Desktop installation has no mail
transport by default — which is why an invitation has to be *presented*, not
merely matched by address (see below).

- **ALTCHA has a key before it is switched on.** The host pack always renders
  `AUTH_ALTCHA_HMAC_KEY`, derived from the installation secret in the
  owner-only `host.secrets.json`, so turning ALTCHA on never leaves the
  challenge endpoint without one. The portal asks for a proof before sign-in as
  well as sign-up; on the LAN — plain HTTP at a private address, not a secure
  context, so no `crypto.subtle` — it hashes in script
  (`lemma-frontend/src/auth/sha256.ts`).
- **`INSTALLATION_SHARED` ends local mode's relaxations.** `ENVIRONMENT` stays
  `local`, so the backend asks `local_relaxations_allowed()` (`app/core/exposure.py`) instead
  of `is_local_mode()` where local mode relaxes something only because nobody
  else is connected: model providers on loopback (refused while shared, and
  Lemma's own service ports are refused always), the loopback CORS defaults,
  the configuration block on `/health/capabilities`, and honouring
  `SURFACE_WEBHOOK_SECURITY_ENABLED=false`.
- **The gateway is held while the stack changes under it.** A new gateway
  answers every visitor `503` (with `Retry-After`) until activation has checked
  the restarted stack *through the gateway itself* — `/runtime-config.js`, and
  `/_lemma/api/auth/altcha/challenge`, which must answer an enabled challenge —
  and the change is committed. Only locald's own check gets through, with a
  per-activation token it strips before forwarding. The gateway is held again
  while sharing is turned off (the stack restarts into local mode before the
  tunnel stops) and while *Who can join* restarts the backend. Enabling still
  restarts the backend and frontend, so running agent turns and open calls are
  interrupted; the Sharing page says so before you choose.
- **Each visitor is rate-limited as themselves.** In Public mode every
  connection reaches the gateway from the tunnel on loopback, so the gateway
  takes the visitor's address from the tunnel — `CF-Connecting-IP` for
  Cloudflare, the last `X-Forwarded-For` entry for ngrok — only from a loopback
  peer, and forwards that. On the LAN the peer is the visitor.
- **ngrok's browser interstitial.** A free ngrok domain shows an HTML warning
  page to a browser's first request. Activation sends
  `ngrok-skip-browser-warning` so the check sees the real answer; visitors see
  the interstitial once, after which their browser carries ngrok's cookie and
  API calls go through.

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
| `invite_only` | Only an address with a pending, unexpired organization invitation — and, where the address is never proven, only with that invitation's id |
| `closed` | Nobody |

An invitation matched by address alone proves nothing where nobody checks the
address belongs to the person typing it: a password sign-up with email
verification off, which is every shared Desktop installation. There the gate
admits an invited address only when the sign-up presents the invitation's id
(`x-lemma-invitation`), which the portal takes from the invitation link the
person followed (`/invitations/<id>/accept`). An unverified account is also
never *shown* invitations by address (`list_user_invitations` answers nothing),
never accepts them automatically, and joins no organization by its email
domain; it accepts an invitation by opening its link.

The **first account on a deployment with no accounts at all** is admitted
whatever the mode -- there is nobody yet who could have invited it. Nothing is
recorded about it: it is an ordinary account from then on. The check is a read,
not a reservation, so two signups racing on an empty database could both get
in. On Desktop that race cannot happen: the app and API listen on loopback
only, sharing is the only way anybody else reaches them, and onboarding creates
the first account before sharing can be turned on. People who already have an
account sign in regardless of the mode. A refusal reaches the auth screen as a
sentence, not a status: *"This Lemma is invite-only. Ask someone already on it
for an invitation."* (`SIGNUP_INVITE_ONLY`), or *"This Lemma is not accepting
new accounts."* (`SIGNUP_CLOSED`).

Defaults: `open` for hosted and self-hosted deployments (the behaviour before
the setting existed), `invite_only` for Desktop. Sharing carries its own
preference, **Who can join** (`who_can_join` in `sharing.json`: `invite_only` by
default, or `open`). It is part of the control snapshot, it can be changed while
sharing is live (`sharing.access`, which restarts only the backend), and the
Public confirmation and LAN warning on the Sharing page describe whichever is
in force.

Every change that lets somebody else in is confirmed in a native dialog the
shell raises itself, never one the page draws: sharing on the local network,
creating a public link, changing *Who can join* to open, turning on **Run
commands on this Mac**, and replacing or removing an OAuth app or bot
credential this Mac already runs with (`NativeConsent` in
`desktop/src/workspace_settings.rs`). The sentence about who may join is built
from the join policy the request will carry — which the shell writes into the
request — not from the saved preference. Local settings sends sharing requests
through the same builder (`consented_sharing_request`), so neither page can set
the Public consent flag.

## What a member gets

An invited member — or anyone at all, if who can join is `open` — gets what a
member of any Lemma gets: a personal workspace, pods they are invited to, and
**a sandbox container inside this Mac's VM**. That container is the boundary,
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
- **No reach into other sandboxes.** Every sandbox sits on the same bridge.
  guestd's `LEMMA-SANDBOX-PEERS` chain rejects any *new* connection from the
  bridge to the bridge -- directly between two containers, or hairpinned to
  another sandbox's published port -- with `br_netfilter` loaded and
  `bridge-nf-call-iptables` set so traffic switched between two containers is
  filtered at all (`ensure_bridge_netfilter`). The backend's way in is a
  published port reached from the host, which arrives on the uplink and is
  unaffected.
- **No IPv6.** Nothing in the guest uses it, and a link-local address would
  otherwise walk around every rule above: `ip6tables` drops everything
  arriving on the bridge (`INPUT` and `FORWARD`), and the VZ guest disables
  IPv6 outright (`/etc/sysctl.d/90-lemma-runtime.conf`; not on WSL, whose
  kernel is shared with the person's other distributions).
- **Bounded.** `--pids-limit 1024`, `--memory`/`--cpus` from the backend, and
  `--oom-score-adj 500` so an overcommitted guest loses a sandbox process
  before the database (core containers are created at -900). No sandbox is
  started with under 2 GiB free on the data disk.
- **Nothing on the host.** A sandbox never runs anything outside the VM.

A function sandbox is additionally read-only with a `noexec` `/tmp`, and its
runtime takes calls only with its own credential: the backend derives a
per-sandbox token, starts the sandbox with it
(`LEMMA_FUNCTION_RUNTIME_TOKEN`, popped before any worker runs) and with the
backend's callback host as the only gateway it will fetch artifacts from
(`LEMMA_FUNCTION_GATEWAY_HOSTS`), and presents it as `X-Lemma-Runtime-Token`
on every call but `/healthz`. The runtime executes whatever artifact it is
sent, so without it a process that could reach port 8090 could have it run
code of its choosing.

All of these rules are installed before the first sandbox starts and
re-checked before every sandbox is created; a guest where any of them cannot be
installed starts no sandbox.

## The host alias

Every sandbox can reach the host through `host.lemma.internal`, which guestd
adds to the container's hosts file pointing at the VM's host gateway. locald
runs two callback forwarders there — the backend's and the frontend's ports,
relayed to the Mac's loopback — and the workspace runtime's callbacks and the
function gateway use them, so every sandbox, whoever's it is, needs the
alias.

It is an explicit per-sandbox flag, `host_access` on `sandbox.ensure`
(`ProviderCreateSpec.host_access` in the backend, passed through the bridge
and hostctl unchanged). It defaults to `true`, and the backend sends it only
when it is `false`, so a guest that predates the flag keeps working.

The alias is **not** a way onto the Mac's own loopback: a server on the Mac's
`127.0.0.1` is not reachable at the gateway address. That is the loopback
relay, below, and only the workspace of the user this Mac's Agent Host is
paired to has it.

The flag controls a name, not a route. What a sandbox can reach at the gateway
address is decided by guestd's firewall, below.

## What a sandbox can reach on the Mac

The host gateway is the Mac's own address on the VM's network, so without a
rule a container could dial any Mac service listening on every interface. guestd
installs one, in `sandbox_firewall.rs`, before it starts any sandbox:
traffic from the sandbox bridge (`nerdctl0`) to the gateway passes a chain
(`LEMMA-HOST-<hash>`, jumped to from the top of `FORWARD` and of `CNI-ADMIN`)
that lets through
replies to connections the Mac opened, the two callback ports, and DNS, which
vmnet serves on the gateway, and rejects everything else (`tcp-reset` for TCP,
so a refused connection fails at once rather than timing out).

- **The ports come from locald.** It names the backend's and frontend's ports
  as `callback_ports` in every `core.*` request, and guestd keeps them in
  `run/callback-ports.json` so a restarted guestd still knows them. A guest
  that has never been told refuses to start a sandbox rather than start one
  that can reach nothing it needs.
- **The same rule for every sandbox**, whoever's it is. The one sandbox that
  reaches the Mac's loopback does so through the relay socket, never through
  the gateway, so it needs no wider rule. Because the
  rule does not vary by container it is keyed on the bridge rather than on
  each container's address.
- **Replaced without a gap.** The chain is named after its contents. New ports
  are built into a new chain in full, jumped to, and only then is the old jump
  and chain removed — the old one must go, because the new chain *returns*
  allowed traffic to `FORWARD`, where the old chain would reject it.
- **Ahead of nerdctl's own rules.** The CNI `firewall` plugin nerdctl runs
  for every container inserts `CNI-FORWARD` at the top of `FORWARD` when the
  first container starts, and that chain accepts everything a container
  sends. A jump from the top of `FORWARD` alone was therefore bypassed from
  the first sandbox on. Every forward jump of ours is also the first rule of
  `CNI-ADMIN`, which the plugin makes the first rule of `CNI-FORWARD` and
  never touches (`FORWARD_HOOKS`); guestd creates it itself when no container
  has run yet.
- **Fails closed.** A guest where the rules cannot be installed starts no
  sandbox.

**What it does not cover.** The rule names the gateway address. Another of the
Mac's addresses, such as its LAN address, is reached through the VM's NAT like
any other machine on the network, and a Mac service listening on every
interface answers there. The core ports inside the guest are a separate rule,
`LEMMA-SANDBOX-ISOLATION`.

## The loopback relay

With [host execution](desktop-host-execution.md) a user's agent starts
`npm run dev` on their Mac, where it listens on `127.0.0.1:3000`, and checks the
result with a browser that runs in that user's workspace sandbox in the guest.
The loopback relay is how that browser reaches the Mac's loopback:

```
Chrome ─proxy─► host_fallback ─unix─► guestd ─vsock 42413─► lemma-vz ─unix─► locald ─tcp─► 127.0.0.1:<port>
(paired user's workspace)        (relay.sock)                (HostLoopbackBridge)  (loopback_relay)
```

- **Per request, not per port.** Chrome in that sandbox is pointed at
  `sandbox_runtime.host_fallback` (`--proxy-server` plus
  `--proxy-bypass-list=<-loopback>`; a PAC is ignored for loopback). A loopback
  port the sandbox is serving stays the sandbox's, so an agent previewing what
  it built there is unaffected. Only a port nothing in the sandbox answers on
  is asked for through the relay.
- **Only the workspace of the user this Mac's Agent Host is paired to.** There
  is no installation owner; the relay leads to *this* Mac, so it follows *this*
  Mac's Agent Host. The backend decides at provision time
  (`host_loopback_policy.is_local_host_users_browser_sandbox`): a workspace,
  owned by a person, on a Desktop install, whose user holds a live (unrevoked)
  pairing with this Mac's Agent Host. locald hands the backend the path of
  that host's config (`DESKTOP_AGENT_HOST_CONFIG_PATH`), and the backend reads
  only `targets[].host_id` from it, each time. A host id is minted by this
  backend at pairing and handed only to the host that paired, so any other
  host -- a teammate's own Mac paired to this Lemma, or an Agent Host somebody
  ran inside their own sandbox -- holds a different id and cannot present this
  Mac's; its user gets host execution on *their* machine but never this Mac's
  loopback. Whether the host is online or switched on is not part of the grant
  (both change while a container lives); locald checks the switch on every
  connection, below. The backend sends `host_loopback: true` on
  `sandbox.ensure` for that sandbox and no other. guestd then bind-mounts its
  relay directory into that container at `/run/lemma-host-loopback` (and
  refuses the grant for a function sandbox). The socket exists only in
  containers it is mounted into, so there is no address anyone else's sandbox
  could dial. The directory is root's and not writable from inside, so the
  sandbox can use the socket but not replace it. This is a separate grant from
  `host_access`.
- **Only while "Run commands on this Mac" is on.** locald reads the Agent
  Host's `host_execution` setting on every connection, as it does the deny
  list, and admits nothing while it is off: there is then no server of the
  agent's on the Mac to check. Turning it off closes the relay for the next
  request, with no restart.
- **Loopback only, and the port is all that is asked.** A request is digits
  and a newline. guestd refuses anything else, and ports below 1024, before
  opening vsock. locald connects to `127.0.0.1`, then `::1`, on that port —
  never a name and never another address.
- **Only a server the agent started.** locald connects only if every process
  listening on the port descends from the Agent Host process it supervises --
  the exec-server, the coding agents it runs, and whatever they start
  (`loopback_relay/owner.rs`: each process's listening sockets through
  `libproc`, then the parent walk). The person's own database, a local admin
  page, a password manager's helper -- anything the agent did not start -- is
  refused as "not started by Lemma's agent", whether or not it is on any list.
- **Not Lemma's own ports.** locald refuses, re-reading the list on every
  connection: the managed runtime's backend, frontend and PostgreSQL, Redis
  and SuperTokens forwards; every loopback health URL in the host pack; the
  sharing gateway and the tunnel's local API (ngrok's inspection port,
  cloudflared's metrics port); and the Agent Host's MCP relay ports, from its
  `mcp-relay/*.json` endpoint files. Privileged ports are refused here too.
  A refusal reaches the sandbox as `error <reason>`, and Chrome shows a failed
  load.
- **Not after the switch goes off.** A relay already carrying bytes re-reads
  the switch every second and ends when it is off (or the Mac is unpaired),
  and a relay nothing has crossed for 30 minutes is ended.
- **Not for a public page.** Chrome keeps public pages off `localhost` by the
  address a request resolves to, which it cannot see through a proxy. So
  `host_fallback` refuses (403) a request bound for the Mac whose `Origin` is
  not loopback, or that Chrome marks `Sec-Fetch-Site: cross-site` -- a link or
  form on a public page. The agent typing a URL, and a loopback page calling
  another loopback port, go through. Each proxied HTTP request has its own
  upstream connection, so a kept-alive browser connection cannot carry a
  request to the wrong machine.
- **Nothing in lemma-vz decides anything.** It carries bytes between guest
  vsock streams and locald's socket (`run/host-loopback.sock`, mode 0600).

**What it does not cover.** While the switch is on, anything in the paired
user's workspace sandbox can reach a server the agent started on the Mac's
loopback: the socket is mounted for the sandbox, not for Chrome alone (every
process there runs as the same user, so there is no browser-only uid to give
it to). Every run in that user's workspace shares that sandbox, including a
run started by an inbound channel message that resolved to them: such a run
cannot execute on the host, but it can reach the agent's servers while the
switch is on. A server that detached itself from the agent (`nohup … &` in a
shell that then exited) is reparented to `launchd`, no longer descends from the
Agent Host, and is refused. The ownership check only sees this user's
processes, and is made just before connecting, not held for the connection. A
WebSocket or HTTPS tunnel (`CONNECT`) carries no headers the proxy can read,
so the cross-site refusal covers plain HTTP only. A `curl localhost:3000` in
the sandbox's shell does not go through the relay: the fall-through is
Chrome's proxy, not the shell's.

**Windows.** The WSL guest runs guestd per request and never binds the relay
socket, so the sandbox finds no socket, the fall-through is not
started, and `localhost` stays the sandbox's. Before the relay, a loopback miss
was retried on the host alias; that path is gone on every platform.

A grant is fixed into the container when it is created. The backend asks the
policy each time it provisions the sandbox, and guestd compares the grants a
running container was made with (its `lemma.work/host-access` and
`lemma.work/host-loopback` labels) with the ones a `sandbox.ensure` asks for,
replacing the container when they differ rather than reusing it with the old
reach. So pairing this Mac's host to a different account takes effect the next
time the workspace is provisioned (after it was released, for instance), while
locald's switch check applies at once.

**Containers created before this** keep the arguments they were created with
until `sandbox.ensure` next replaces them; a guest restart does.

## The Tauri IPC origin rule

The workspace page is a remote origin to Tauri, and reaches the shell only
through a capability that names it. For the local workspace that capability is
minted at runtime, on the workspace's exact origin with its port
(`local_workspace_capability` in `desktop/src/workspace.rs`), once locald has
named it; `capabilities/workspace.json` itself lists only the hosted site. A
shared origin — the LAN address or the tunnel host — is in no capability, and
that ACL is what keeps it out. The Agent Host commands' Rust check
(`require_agent_host_caller`) compares the page with the origin the app
navigated to, which *while sharing is on is the shared origin* — so on its own
it would not refuse one; it exists for a capability written too loosely, not
for sharing. It compares full origins, port included.

Why exact, and not `http://app.lemma.localhost:*`: on macOS pod apps are framed
through **alias ports on the workspace's own host** (see
[Desktop architecture §6.2](desktop.md#62-pod-apps)) — user-authored code on
`http://app.lemma.localhost:<alias port>`, inside the very window the
capability is granted to. Tauri judges a frame's IPC by the frame's own origin,
so a port wildcard would have matched it; the exact origin does not, and every
Rust caller check compares the whole origin as well. The invoke key Tauri
injects into the top frame only is a further barrier, not the one relied on.

What an alias origin does share with the workspace is the *host*, and cookies
ignore ports: a framed app can read and overwrite the workspace's non-HttpOnly,
host-only cookies (SuperTokens' `sFrontToken`, which carries the access-token
payload and no credential) and set cookies the API on the same host will
receive. That is no wider than what any pod app already has: every app host is
inside the `Domain=lemma.localhost` session cookie's scope, so an app can toss
a cookie at the API from its canonical host too, and it acts as the signed-in
person through `/_lemma` by design. HttpOnly session cookies stay unreadable.
An alias listener binds loopback only, forwards only to this installation's
app ingress, and answers 421 to any `Host` but its own, so a page that rebinds
its own name to 127.0.0.1 gets nothing from it.

The This Mac settings commands check more narrowly: local mode, the loopback
workspace origin this app navigated to, and — asked again at the moment of the
call — a host that resolves to loopback and nothing else
(`page_host_is_loopback`). The shipped host is `app.lemma.localhost`, loopback
by resolver convention, so nothing in this rule depends on DNS; the re-check
matters only for a development override served on some other name.
The app's own window, once sharing has moved it to the shared address, is
refused, turns sharing off from the native Local settings, and does not try
the shell at all (`onShellOrigin` in `lemma-frontend/src/desktop/bridge.ts`).

In local mode the main window never becomes a browser for another site: a
top-level page load on a host that is not this installation's is handed to the
system browser and the window returns to the workspace (`main_frame_leaves_app`
in `desktop/src/navigation.rs`; iframes are unaffected). Release builds open no
web inspector unless `LEMMA_DESKTOP_DEVTOOLS=1`.

A visitor's browser can drive the shared Lemma; it can never invoke the
desktop shell, the Agent Host or anything that touches the local stack. See
[The privilege boundary](agent-host.md#the-privilege-boundary).

### Signing in the app through the system browser

A hosted workspace signs the app in through the system browser (a request id,
PKCE, then `lemma://auth/complete`). The browser half never completes a request
on its own: it shows the request's short code — which the app shows too — and
the account it will sign in, and waits for a click. A request id arrives in a
URL, and a signed-in browser pointed at somebody else's used to complete it and
hand them that person's session.

## What is not covered

- **UDP between sandboxes that were already talking.** The peer rule
  refuses *new* connections; conntrack state from before an upgrade survives
  until it expires.
- **A function sandbox's own cache.** Function code runs as the same user as
  the runtime that unpacked its artifact, so it can alter the cached copy of
  another function *of the same pod* for as long as that sandbox lives. Only
  that pod's code runs there, and the credential and gateway allow-list keep
  anything from outside from adding to it.
- **Containers created before an upgrade** are not reused with the arguments
  they were created with. Every container carries `lemma.work/hardening`
  (`SANDBOX_HARDENING_VERSION` in guestd) and its grants as labels, and the
  next `sandbox.ensure` replaces a running one whose hardening is older or
  whose grants differ from the request. The replacement is made only after
  everything that can fail before `run` has passed, and a running container is
  renamed aside rather than removed, so a failed start puts it back; a
  resident guest that died mid-swap settles it when it next starts (removing
  the old container where the new one exists, restoring it where it does
  not). A request for a newer epoch replaces a running container the same
  way; an older one is refused as a generation conflict.
