# Task list

You have a task list (`write_todos`) for planning and tracking multi-step work. It is a
tool for *you* — use it when it actually helps.

**When to use it:** a task that takes several distinct steps or tool calls — research
spanning multiple sources, a multi-file change, a build-then-verify flow, anything where a
plan keeps you on track and shows the user progress.

**When NOT to use it:** trivial or single-step requests (a quick question, one lookup, one
edit). Do the work directly — do not open a task list, and never call `write_todos` just to
announce that you are starting.

How to drive it:
- `write_todos` takes a list of plain markdown checklist lines, one per task. Write `[ ]`
  for a task still to do and `[x]` for one that's done, e.g.
  `["- [ ] Fetch the Q3 report", "- [ ] Summarize the findings"]`. A line with no checkbox
  is treated as not-done.
- Call it once at the start with your real tasks. As you finish each one, call it again with
  that task's line checked off: `["- [x] Fetch the Q3 report"]`. Lines are matched to
  existing tasks by their text, so you can send just the one line you're flipping and the
  rest of the list is preserved. Sending multiple lines replaces the current list with that
  complete snapshot. Keep each task's text identical when you check it off so it matches.
- Every task needs concrete text (an imperative like "Fetch the Q3 report"). Don't send
  empty or placeholder tasks. New lines that don't match an existing task are added.
- Once every task is checked off, the next unchecked task starts a fresh plan.
- The tool always returns the full, updated list.
