# Recipe — agent chat in an app

Put a pod agent in front of users without hand-rolling the chat/working-output UI.
Three options, smallest first. (← back to `apps.md`)

## HTML / no framework — web components

Load the opt-in UI bundle *after* the client; pod context comes from the injected
`window.__LEMMA_CONFIG__`.

```html
<script>
  // Load the client bundle, then the opt-in UI bundle — both from the API origin
  // in window.__LEMMA_CONFIG__ (a relative src 404s once saved as an app). The
  // custom elements register on load and upgrade the tags already in the DOM.
  (function () {
    var cfg = window.__LEMMA_CONFIG__ || {};
    var base = (cfg.apiUrl || window.location.origin).replace(/\/$/, "");
    function load(path, next) {
      var s = document.createElement("script");
      s.src = base + path; s.onload = next || null; document.head.appendChild(s);
    }
    load("/public/sdk/lemma-client.js", function () {   // window.LemmaClient + config
      load("/public/sdk/lemma-ui.js");                  // registers the elements below
    });
  })();
</script>

<!-- One-shot run: working state → final output (schema-parsed if the agent returns structured output) -->
<lemma-agent-task agent="triage" input='{"ticket_id":"123"}' auto-run></lemma-agent-task>

<!-- Full multi-turn chat: message list + composer; conversation created on first send -->
<lemma-agent-thread agent="support"></lemma-agent-thread>
```

- `<lemma-agent-task>`: attrs `agent`, `pod` (optional), `input` (string/JSON),
  `auto-run`, `parse-output` (default true); method `el.run(input?)`; emits a
  `lemma-output` event on completion (`e.detail` is the parsed output).
- `<lemma-agent-thread>`: attrs `agent`, `pod`, `conversation-id` (optional);
  method `el.send(text)`.
- Both are Shadow-DOM and themeable from the host via CSS custom properties
  (`--lemma-bg`, `--lemma-accent`, `--lemma-radius`, …) and `::part(...)`.
- The current thread component renders tool activity as generic event lines. It
  does not yet render `ask_user`/`request_approval` controls or embedded widgets;
  use a custom renderer for those interactions.

## React — preset components

`<AgentTask>` / `<AgentThread>` are render-prop presets (they wrap `useAgentTask` /
`useConversationMessages`; `children` receives the hook result):

```tsx
import { AgentTask, AgentThread } from "lemma-sdk/react";

// one-shot: working → final output
<AgentTask client={client} podId={client.podId} agentName="triage" input={{ ticket_id }}>
  {(task) => task.isRunning ? <Spinner/> : <Result data={task.output} />}
</AgentTask>

// multi-turn chat: you own the list + composer
<AgentThread client={client} podId={client.podId} agentName="support">
  {(t) => <Messages items={t.messages} onSend={t.send} finalText={t.finalOutputText} />}
</AgentThread>
```

## React — raw hooks (full control)

```tsx
import { useConversations, useConversationMessages } from "lemma-sdk/react";

const { conversations, create } = useConversations({ client, podId: client.podId, agentName: "support" });
const { messages, send, finalOutputText, isStreaming } =
  useConversationMessages({ client, podId: client.podId, agentName: "support", conversationId });
```

## The one gotcha — reading the reply

A single assistant turn emits **several** `role:"assistant"` messages split by
`kind`: `thinking`, `tool_call`, `tool_return`, `notification`, `text`. The
user-facing answer is the `text` message with `metadata.is_final_answer === true`
(content in its `.text` field). **Never** grab the first/last assistant message.
In React use `useConversationMessages().finalOutputText`; raw shapes are in the SDK
README.

> Read `/sdk/lemma-typescript/src/react/{useAgentTask,useConversationMessages}.ts`
> for exact return fields.
