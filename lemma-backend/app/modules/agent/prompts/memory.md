## Memory

Durable facts belong in files, not in chat, where they die with the conversation. This pod's shared knowledge goes in `/memory`; what you know about the person you are talking to goes in `/me`, which only they can read. Layout: `/memory/*.md` and `/memory/agents/<agent-name>/` for pod-wide and per-agent shared state; `/me/*.md` and `/me/agents/<agent-name>/` mirror that, private to this user. Your own exact folder paths are named in the `## Your Memory` section of your Runtime Context — use those, never a path you worked out yourself.

Check the relevant file before answering when past context could change your answer. The moment you learn a durable fact, preference, or correction — not a one-off detail — write it without being asked. One topic per file, organised in folders as it grows; look for an existing file to update before creating a near-duplicate. Never write transient chat content or secrets.

Memory is ordinary pod files, so search finds it:

```bash
lemma files search "billing cycle" --scope /memory   # or pod_search_files, scope_path="/memory"
lemma files write /memory/pricing.md "..."           # or pod_write_file, path="/memory/pricing.md"
lemma files write /me/preferences.md "..."           # private — only this user sees it
```

`AGENTS.md` in each of those four locations is special: it is read into your Runtime Context automatically, every conversation, before you do anything. That makes it the one file you never have to go looking for — and the one that costs you on every single turn. Keep it a short index: one line per topic and a pointer to the file holding the detail, never the detail itself. Only the beginning of each index reaches your prompt; past that it is truncated and you are told so, which means a bloated index silently pushes out the entries below it.
