# lemma-streamer

The live view of a machine's display over WebRTC. It runs inside the machine,
captures the X display (`:99`), encodes it once per codec for every viewer,
and sends it through TURN. VNC stays as the fallback for viewers whose network
cannot reach TURN.

It is started on demand by the browser relay (`start-streamer`), listens on
**loopback only** (`127.0.0.1:${LEMMA_BROWSER_STREAM_PORT:-4860}`), and exits
after two minutes with nobody watching.

## Layout

| Package | What it does |
|---|---|
| `internal/ticket` | Verifies the per-viewer ticket the API mints |
| `internal/input` | The closed input set: strict parsing, ranges, rate limits |
| `internal/inject` | XTest injection of parsed commands; clipboard and resize helpers |
| `internal/capture` | XShm (or GetImage) grabs, XDamage, XFixes cursor and clipboard |
| `internal/convert` | BGRA → I420, pure Go |
| `internal/encode` | libx264 (constrained baseline) and libvpx (VP8), cgo, linked statically |
| `internal/rate` | Bitrate from the viewers' congestion estimates; frame rate from spare CPU |
| `internal/session` | Pion peer connections, signalling, the capture → encode pipeline |
| `cmd/lemma-streamer` | The binary |
| `cmd/streamcheck` | A headless viewer for tests: through the API, or straight to a streamer |

## Security model

The property this keeps from VNC: **a viewer's input is a closed, validated
set, and a viewer who is only watching has no input path at all.**

- **Ticket.** The API mints one per viewer:
  `b64url(payload) "." b64url(HMAC-SHA256(key, b64url(payload)))`, payload
  `{"sub","machine","mode":"view"|"control","exp","jti"}`. The key is derived
  for this one machine and delivered to `LEMMA_STREAM_KEY_FILE` (default
  `/tmp/lemma-relay/stream.json`, `{"key":"<hex>","machine":"<id>"}`), re-read
  when it changes. Checks, in order: signature (constant time), machine id,
  expiry (5 s skew), mode, replay (`jti`). No key file means every ticket is
  refused. The viewer's browser never sees a ticket: the API inserts it into
  signalling.
- **View means no input.** Data channels are opened by the viewer. `events`
  is streamer → viewer only (anything sent to it is dropped). `input` is
  accepted only from the current controller; anyone else's is closed unread.
  Any other label is closed.
- **One controller.** The newest control ticket wins; the previous controller
  receives `{"t":"revoked"}`, loses its input channel, and every key or button
  it held is released.
- **Closed set.** Each `input` message is one strict JSON object, no unknown
  fields, no trailing data, at most 128 bytes (text 4 KiB, paste 1 MiB):

  | `t` | fields | rule |
  |---|---|---|
  | `m` | `x`, `y` | inside the current screen |
  | `b` | `b`, `d` | button 1–3 |
  | `w` | `dx`, `dy` | each −10…10, not both 0 |
  | `k` | `k`, `d` | keysym: Latin-1, the 0xfe00–0xffff block, or Unicode keysyms |
  | `txt` | `s` | ≤ 256 code points, no control characters but `\n` `\t` |
  | `paste` | `s` | ≤ 1 MiB; sets the clipboard, then Ctrl+V |
  | `size` | `w`, `h` | one of `LEMMA_STREAM_PRESETS` |

  Binary frames and everything else are dropped and counted. 1000 messages/s,
  120 wheel notches/s. Nothing here runs a command, opens a file or reaches
  the page's script.

## Signalling

`WS /signal`, JSON text frames ≤ 64 KiB. The first message must be `hello`,
within 5 s.

Viewer → streamer (in production: the API, on the viewer's behalf):

| `t` | fields |
|---|---|
| `hello` | `ticket`, `ice_servers` `[{urls, username, credential}]`, `policy` `relay`\|`all` (default `relay`) |
| `offer` | `sdp` (again later to renegotiate) |
| `candidate` | `candidate`, `sdpMid`, `sdpMLineIndex` |
| `control` | `ticket`: a fresh control ticket, to take control |
| `release` | give control back |
| `bye` | |

Streamer → viewer: `ready {mode}`, `answer {sdp, codec}`, `candidate {…}`
(`candidate: null` at the end of gathering), `mode {mode}`,
`error {code, message}` followed by a close.

Media: H.264 constrained baseline (`profile-level-id=42e01f`,
`packetization-mode=1`) when the offer has it, else VP8. NACK, PLI/FIR (a
keyframe, at most one per 300 ms), and transport-wide congestion control
feeding the encoder's bitrate (the slowest viewer of a codec sets it).

Events on the `events` channel: `hello {w,h,codec}`, `size {w,h}`,
`cursor {id,x,y,w,h,png}` (the PNG only the first time an id is sent; `x`,`y`
is the hotspot), `pos {x,y}`, `control {you}`, `revoked`, `clip {s}`
(controller only), `agent {driving}` (the agent ran a browser command in the
last few seconds: `/tmp/lemma-browser/agent-active` was touched).

`GET /health` → `{viewers, controller, codecs, fps, bitrate, capture, w, h}`.

## Pipeline

Capture ticks at 30 fps; with no damage since the last grab it sends a frame
only once a second. Capture converts into a frame that each codec's encoder
takes from a one-frame slot: a frame that arrives while the encoder is busy
replaces the waiting one, so a slow encode drops frames and never queues.
When the machine's spare CPU (cgroup `cpu.max`/`cpu.stat`, else `/proc/stat`,
over 5 s) falls below 0.25 core the frame rate steps to 15, below 0.1 to 5,
and climbs back with hysteresis.

## Build and test

```bash
# unit tests (pure packages; anywhere with Go 1.25)
go test ./internal/ticket ./internal/input ./internal/rate ./internal/convert
go test ./internal/input -run x -fuzz FuzzParse -fuzztime 60s
go test ./internal/ticket -run x -fuzz FuzzVerify -fuzztime 60s

# everything, encoders included, and both binaries into ./out
docker build --target out -o type=local,dest=out .

# integration: a workspace image + coturn, see the script's header
integration/run.sh [lemma-workspace:<tag>]
```

The encoders link x264 and libvpx statically, so the binary needs only libc
and libm and runs on both the Debian image and the E2B template.
