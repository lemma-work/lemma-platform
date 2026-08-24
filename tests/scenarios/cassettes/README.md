# Recordings

What the real Telegram, Google, GitHub and Slack said, the last time this suite
asked them. Written by `make scenarios-record`, served by
`make scenarios-replay`, and **committed on purpose**.

A diff in one of these files is a third party changing its API. That is the
whole reason for recording rather than writing a stand-in by hand: an imitation
we maintain ourselves keeps returning last year's shape and the suite stays
green. A recording cannot — re-record it and the diff is the news.

So review them like code. A change here that nobody expected is worth
understanding before it is merged.

Two things the format guarantees, both enforced in `harness/egress.py`:

- **Nothing is streamed.** mitmproxy will pass a response straight through
  without keeping it, and a recording made that way replays as `200 OK
  (content missing)` — a response that satisfies a status assertion and holds
  nothing at all.
- **A request nobody recorded is killed, not forwarded.** A replay run cannot
  reach the real internet, so a gap in a recording is an error rather than a
  silent live call with real side effects.

They are `mitmproxy` flow files, so they are binary. `mitmdump -r <file>` prints
one as text if you need to read it.
