# The live socket

The client half of the pod live socket (`lemma-backend/docs/modules/live.md`)
is the web's own, imported where it lives: `@web/live/socket` and
`@web/live/conversation-stream`.

`pod-live.ts` is the phone's own: the same exports as the web's, with the
app's token instead of a cookie, the URL built by hand, and waking when the
app comes back to the front. `socket-conversations.ts` is the web's file
over the phone's `pod-live.ts`, so it stays here; it differs from the web's
only by the `"use client"` line.
