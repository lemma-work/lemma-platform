# The chat thread

## The web's, imported

The chat runs the web's own thread modules where they live
(`@web/thread/turns`, `approval`, `display-resource`, `tool-cards`,
`tool-name`, `memory-notes`, `pending-sends`, `queued`, `model-setup`,
`stuck-run`, `transcript-state`, `send-message`, `turn-identity`, `drafts`,
`channel-reply`, and `@web/data/agent-names`; see `../../web-imports.js`).
They turn a conversation's messages into turns a person reads and have no
client or DOM in them, so the two apps cannot disagree about what a
transcript says. Drafts and the reply choice are kept in `kept.ts` (a store
in memory, written to a file in the app's documents, emptied on sign out),
handed to the web's functions where the web hands them `localStorage`.

## The web's, with the phone's store or files

- `attachments.ts`: the web's rules and wording, for a file picked on the
  phone (`PickedFile`) rather than a browser `File`.
- `runtimes.ts`, `previews.ts`, `trouble.ts`: the parts of the web's
  `data/runtimes.ts`, `data/live.ts` + `data-view.tsx`, and `data/trouble.ts`
  the chat needs.
- `derive.ts`: what `LiveConversation` decides before it draws (the
  transcript and the tray, the composer's note), out of the component.

## The phone's own

- `use-chat.ts`: the conversation, on the SDK's `useAssistantSession` (the
  web's hook), over the live socket; a sample engine runs the same rules.
- `view.tsx`, `cards.tsx`, `interaction.tsx`, `resources.tsx`, `dock.tsx`,
  `composer.tsx`, `model-picker.tsx`: the web's `turn-row.tsx`,
  `tool-card-view.tsx`, `interaction-card.tsx`, `resource-card.tsx`,
  `interaction-dock.tsx`, `composer.tsx` and `model-picker.tsx`, drawn for a
  phone.
- `blocks.ts`: markdown cut into blocks, so a streaming reply redraws only its
  last block and code, tables and quotes each carry a Copy.
- `glyphs.tsx`, `clipboard.ts`, `hand-over.ts`.

## Design v2

- `look.tsx`: the Lemma's colour (`ui/identity.ts`) and the steps' times,
  carried down the chat.
- `run-line.tsx`, `steps.ts`, `breathing.tsx`: the step line under a run,
  its fold into "Worked for … · N steps", the steps drawer.
- `reveal.ts`: a streaming reply let in word by word (`StreamingMarkdown` in
  `ui/markdown.tsx` draws it).
- `consequence.ts`, `hold-button.tsx`: which approvals are held for a second,
  and the hold.
- `message-files.ts`: a person's attachments and the Lemma's pictures as
  files; the pieces are `ui/files-in-chat`, shared with groups.
