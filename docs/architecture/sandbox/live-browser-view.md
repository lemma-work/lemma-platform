# The live browser view

How a person watches, and drives, the Chrome in their machine. Two transports
share one pane: **WebRTC** where it can connect, **VNC** everywhere else. This
page says what each path is, what the security boundary is on each, how the
screen's size is decided, and how the rollout is controlled. For numbers, run
the stream bench (below); this page carries none on purpose.

## The two paths

```mermaid
flowchart LR
  subgraph Browser
    PANE[Computer pane<br/>live-view.tsx]
  end
  subgraph API
    VIEW[WS /machines/{ref}/browser/view<br/>?transport=vnc or rtc]
    TURNP[TurnProvider<br/>Cloudflare or coturn]
  end
  subgraph Machine
    RELAY[browser relay :4850<br/>/vnc and /rtc]
    WSF[websockify :5901]
    TVNC[TigerVNC X0tigervnc :5900]
    STR[lemma-streamer :4860<br/>capture, x264/VP8, XTest]
    X[Xvfb :99 + Chrome]
  end
  TURN[(TURN relay)]
  PANE -- VNC frames --> VIEW --> RELAY --> WSF --> TVNC --> X
  PANE -- signalling only --> VIEW
  RELAY -- signalling only --> STR --> X
  PANE <-. media + input, relay-only .-> TURN <-. media .-> STR
  VIEW --> TURNP
```

**VNC** is the picture over WebSockets: browser → API → relay → websockify →
TigerVNC's scraping server on the Xvfb display. It is a pull protocol carried
as JPEG rectangles, so its frame rate is bounded by the round trip and its
bytes by the link. Every leg runs **without permessage-deflate**
(`server_limits.WS_PER_MESSAGE_DEFLATE`, the relay's uvicorn, `compression=None`
on both upstream connects): the frames are already JPEG or zlib, and
recompressing them was the relay's largest cost. noVNC asks for quality 6 and
compression 6 (`live-screen.tsx`).

**WebRTC** carries no picture through the API. The view socket with
`transport=rtc` carries the signalling — an offer, an answer, ICE candidates —
to the streamer in the machine (`sandbox-images/streamer`, Go and Pion), which
captures the display, encodes it once per codec (H.264 constrained baseline
`42e01f`, or VP8) for every viewer, and sends it over the peer connection
through TURN. Frames are dropped rather than queued when the machine is busy,
a keyframe is sent on PLI and on join, and the bitrate follows the viewers'
congestion estimates.

The pane picks a transport per mount (`stream.ts`, `chooseTransport`):

| Server says (`BROWSER_STREAM`) | This browser has WebRTC | Pane uses |
|---|---|---|
| `vnc`, or the user is outside the rollout | — | VNC |
| `auto` | yes | WebRTC, then VNC if ICE has not connected 5 s after the machine's config |
| `auto` | no (the probe fails, as on a WKWebView without it) | VNC |
| `webrtc` | yes | WebRTC only, retried; for testing the streamer alone |

An image built before the streamer existed answers `/rtc` with close code
**4427**, and so does a machine whose streamer would not start, and the API
when no TURN relay is configured; the pane treats 4427 as "use VNC" and says
nothing. A streamer that failed is not 1011: the pane retries 1011, and each
retry ran the start again and minted TURN credentials for it.

## Opening the view

Three things about the first seconds, each of which once left a pane stuck on
"Connecting…":

- **The API accepts the view socket as soon as it knows who is asking and
  which machine** — before it starts the browser or sets up WebRTC. A browser
  lets one WebSocket per host be in CONNECTING at a time (RFC 6455 §4.1), so a
  handshake held open for a cold browser start queued the terminal's stream
  and the pod's live socket behind it. Refusals after that point still arrive
  as close codes (`browser_view_hangup.refuse` closes an accepted socket the
  same way), 4427 included.
- **The 5 s WebRTC deadline starts when the machine's `config` message
  arrives** (`rtc-session.ts`), not when the socket opens. Everything before
  `config` is the machine starting its browser, which can take longer than the
  deadline on its own; ICE starts at `config`.
- **The relay starts one browser per session at a time**
  (`browser_relay/chrome.py`, `ensure_port`). The view socket, the toolbar's
  tab poll and the keepalive arrive together, and two `agent-browser` starts
  racing each spawn a daemon: the second daemon's Chrome finds the profile
  locked and exits, and its daemon later deletes the live daemon's socket, so
  Chrome runs on with nothing able to reach it. Starts, `open` and the
  keepalive share a per-session lock, and a start re-checks for a live
  browser once it holds it. If a start still reports a wedged or collided
  daemon ("exited during startup"), the relay stops every `agent-browser`
  daemon *and* the Chrome on that session's profile (matched by its
  `--user-data-dir`; SIGTERM, then SIGKILL), and tries once more. Chrome runs
  with `--hide-crash-restore-bubble` (`lemma-ensure-display`), so a browser
  killed this way does not come back asking to restore pages.

## The input boundary

The property both paths keep: **a viewer's input is a closed, validated
message set, and a watcher has no input path at all.**

- **VNC.** RFB's client vocabulary is fixed by the protocol. In `view` mode
  the relay drops every message that is not one of the three that only ask for
  a picture, parsed message by message (`stream_proxy.py`).
- **WebRTC.** The streamer accepts a viewer only with a **ticket** the API
  signs: `b64url(payload).b64url(HMAC-SHA256(key, b64url(payload)))`, payload
  `{sub, machine, mode, exp, jti}`, valid for 60 seconds and once. The key is
  per machine and derived from the runtime credential key and the provider id
  (`browser_stream.stream_key`), delivered beside the relay token; a key read
  out of one machine signs nothing for another. **The browser never holds a
  ticket.** It may send five messages on the socket — `offer`, `candidate`,
  `take`, `release`, `bye` — and the API filters everything else
  (`browser_view_rtc.from_browser`). Asking to drive is `take`; the API mints a
  control ticket only for a viewer who may drive.
- In the streamer, a `view` peer's `input` data channel is **closed unread**.
  The controller's channel accepts only `m` (pointer), `b` (button), `w`
  (wheel), `k` (keysym), `txt` (composed text) and `paste` (clipboard, then
  Ctrl+V; its own budget of two a second, and the text leaves the machine's
  clipboard ten seconds later): strict JSON, unknown fields refused, sizes
  capped, rate-limited, keys released when control changes. Nothing in the set
  runs a command, reaches a file or speaks CDP. There is no size message: the
  display's size goes through the resize lease below, on either transport.
  One controller at a time; a newer control ticket revokes the older one, and
  a control ticket must name the same viewer as the socket's first ticket.
- `/signal` refuses any request carrying an `Origin` header. The relay sends
  none; a page in the machine's own Chrome, which can reach that loopback
  port, always does.
- **Clipboard over VNC is inbound only.** RFB is shared, so the VNC server's
  clipboard updates (ServerCutText) reached every socket, watchers included.
  The server is started with `-SendCutText=0` (TigerVNC) or `-seldir recv`
  (x11vnc): a controller can paste in, and nothing the agent or a driver
  copies is sent out to anyone watching.

**Watching needs `use`.** The screen shows whatever is signed in, so the view
socket — either mode, and the TURN credentials it hands out — needs `use` of
the machine; a pod member with `status` only sees whether the browser runs,
not the picture, not who is watching and not who holds the size
([machines.md](../machines.md#who-may-do-what)).

## TURN

Both ends are **relay-only**: the streamer and the browser each allocate on
TURN and offer no host candidate. Credentials are minted per connection by a
`TurnProvider` (`browser_stream_turn.py`) and expire
(`TURN_CREDENTIAL_TTL_SECONDS`, an hour by default); the long-lived secret
never leaves the API. A relay re-checks them when an allocation is refreshed,
so a view that outlives them reconnects and is minted fresh ones. A viewer
reconnecting to the same machine within a minute is handed the same ones
again (`browser_view_rtc.ICE_REUSE_SECONDS`, in Redis) rather than costing two
more provider calls.

**Relay-only is a cost as well as a default.** Every byte of every WebRTC view
crosses the TURN provider, both legs, and a hosted provider bills that egress;
there is no direct path to fall back on that would be cheaper. It is the right
security default — a sandbox's addresses are nobody's business — and it is the
rollout's running cost, which grows with viewers × minutes watched × bitrate.

- `TURN_PROVIDER=cloudflare`: Cloudflare Realtime TURN
  (`CLOUDFLARE_TURN_KEY_ID`, `CLOUDFLARE_TURN_API_TOKEN`).
- `TURN_PROVIDER=coturn`: a self-hosted coturn with `use-auth-secret`
  (`COTURN_URLS`, `COTURN_SECRET`). Configure `no-loopback-peers`,
  `no-multicast-peers`, and `denied-peer-ip` for private ranges and the
  cluster's own CIDRs, or the relay becomes a way into the network behind it.
- **TLS on 443 is always in the list.** A provider whose answer lacks a
  `turns:` URL on 443 gets one; a coturn list without one is logged. UDP is the
  fast path, and a sandbox fabric or a corporate network may drop it.
- `TURN_PROVIDER=none` (the default) turns WebRTC off for everyone.

## The screen's size

The pane **rescales the picture locally** and never resizes the machine's
display because its own box changed. The display takes one of four
**presets** — Desktop 1280×800, Wide 1440×900, Tablet 820×1180, Phone
390×844 (`browser_relay/display.py`, mirrored in `presets.ts`) — chosen before
connecting or from the toolbar.

A preset is applied by the viewer **holding the resize lease**: the relay, the
one process that sees every viewer's socket, gives it to the first controlling
viewer to ask and keeps it while that viewer's socket is open and in control,
until somebody else takes control. That last clause is for VNC: a VNC control
socket is in control for as long as it is open, because RFB input cannot be
taken back, and without it would hold the size against a WebRTC viewer who had
since taken the wheel. A watcher is told to take control first; a second
controller is told somebody else holds the size. The lease is the only way to
change the size: the streamer takes no size message. When the last viewer leaves the display goes back to its
resting size, the Desktop preset, so an agent's screenshots never inherit a
phone's shape.

**Native density.** On a machine with at least two vCPUs, and when
`BROWSER_NATIVE_DENSITY` allows it, Chrome runs at a device scale factor of 2
and a preset is doubled in device pixels. The server's half of the decision is
delivered as `/tmp/lemma-relay/display.json` on every session, on the agent's
path as well as the viewer's (`workspace_runtime_bundle._ensure_browser_proxy`);
the machine's half is its own CPU quota.

**Density is fixed per machine when its display is created.**
`lemma-ensure-display` decides it when it starts Xvfb, writes it beside the X
server's lock, and every later run reads it back for as long as that server
lives. Density is part of the browser's launch digest, and a digest change
restarts Chrome (`close --all`), so a density that could change under a running
display restarted the agent's Chrome mid-task — tabs and all — the first time
somebody opened the screen. Now opening, closing or reconnecting a view never
changes it; a change to `BROWSER_NATIVE_DENSITY` reaches a machine the next
time its display is made (a resume, a new machine).

It is the agent's Chrome too: there is one browser, so on a dense machine the
agent's screenshots are taken at twice the pixels in each direction, which a
vision model pays for. That is the trade `BROWSER_NATIVE_DENSITY` controls.

**One encoder per codec.** Every viewer of a codec shares one encoder, whose
bitrate is the minimum of the viewers' congestion estimates: one viewer on a
poor link lowers the picture for everybody watching that machine, in exchange
for the machine's CPU staying flat as viewers join. **H.264** is preferred
because Safari and iOS decode it in hardware; it is patent-encumbered, which
is a licensing question for whoever ships the encoder. VP8 is royalty-free,
mandatory in every WebRTC stack, and already the fallback
([streamer README](../../../lemma-backend/sandbox-images/streamer/README.md)).

## The toolbar

Back, forward, reload, an address bar, the tabs, the preset, and Take control /
Give back (`screen-toolbar.tsx`). The browser commands are a closed set
(`POST /machines/{ref}/browser:command`, relay `/browser:command`): navigation goes
through `agent-browser`, which keeps the agent's tab bookkeeping right, and
only `http`/`https` addresses open. "Agent is driving" shows while the agent's
`agent-browser` commands are arriving (the wrapper touches
`/tmp/lemma-browser/agent-active`; the relay's own commands do not).

While connecting, the pane shows the last picture it saw of this machine,
dimmed, from this browser's own storage; nothing leaves the device.

## Measuring it

Each pane reports how its session went once it ends
(`POST /machines/{ref}/browser:beacon`, logged as
`workspace.browser_view.stream_session.observed`): transport, fallback reason,
first frame, frame rate, latency, bitrate, codec, relay protocol.

The stream bench measures delivered frame rate, glass-to-glass latency,
key-to-echo, bytes and per-process CPU in the machine, scenario by scenario,
under shaped networks:

```bash
cd lemma-backend
STREAM_BENCH_CPUS=2 uv run pytest -s app/modules/workspace/tests/e2e/stream_bench_stack.py
node scripts/stream-bench.mjs --target out/stream-bench/target.json --transports vnc,webrtc
```

WebRTC's network profiles are applied with `tc netem` on the TURN container,
since its media never crosses the runner's TCP shaping proxy. The end-to-end
lane covers the paths themselves:
`app/modules/workspace/tests/e2e/test_browser_stream_e2e.py`.

## Rollout

`BROWSER_STREAM` is `vnc` by default. The rollout is
`BROWSER_STREAM=auto` with `BROWSER_STREAM_USERS` (the team), then a pod's
users, then `BROWSER_STREAM_PERCENT` (a stable hash of the user id), then 100.
The desktop app stays on VNC wherever its WebView fails the `RTCPeerConnection`
probe.
