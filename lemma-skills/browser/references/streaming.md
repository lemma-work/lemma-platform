# Live Streaming

agent-browser can stream a session's viewport over a localhost WebSocket
(`agent-browser stream enable`). On a Lemma machine, do not use it.

**Related**: [commands.md](commands.md) for the command reference,
[SKILL.md](../SKILL.md) for how the Lemma browser differs.

## Why not

- **The person already sees the browser.** Lemma shows the machine's display —
  the same Chrome you drive — in its own viewer, with take-control and hand-back.
  There is nothing to enable and nothing to connect.
- **The stream reaches nobody.** It binds `127.0.0.1` on the machine. Nothing
  outside the machine can open it, and it is not how the viewer works.
- **It costs the machine.** Every frame is a JPEG encoded on the machine's CPU,
  which the person's viewer and your own commands then compete for.

## What to do instead

| You want | Do |
|---|---|
| The person to watch | Nothing — drive the browser; they see it live |
| A picture for yourself | `agent-browser screenshot "$PWD/shot.jpeg"`, then `view_image` |
| A recording | `agent-browser record start "$PWD/run.webm"` ([video-recording.md](video-recording.md)) |
| The person to open your dev server | `expose_port(port=<port>)`, and reply with the preview URL |
| A page the person keeps | `save-webpage <url> --formats pdf`, then copy it to pod files |
