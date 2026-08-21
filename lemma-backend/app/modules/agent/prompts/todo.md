# Task list

`write_todos` tracks multi-step work. Use it when a task takes several distinct steps — multi-source research, a multi-file change, a build-then-verify flow. Skip it for single-step requests, and never call it just to announce you're starting.

It takes markdown checklist lines: `["- [ ] Fetch the Q3 report", "- [ ] Summarize findings"]`. Every task needs concrete imperative text. Sending several lines replaces the whole list; sending one line matches an existing task by its text and flips just that one. The tool returns the full updated list.

Writing the plan is the easy half. The half that matters:

- **Check an item off the moment it is done, before you start the next one.** `["- [x] Fetch the Q3 report"]` — one line, the task's own words. Not at the end of the run, not in a batch, not "once I'm sure".
- **Reuse the exact text you planned with.** That is how the tool finds the task. Reworded lines are matched when they're close, but they don't have to be guessed at if you copy the line back.
- **The list is not a note to yourself.** Lemma stores it and the person watching this conversation reads it to know where you are. An item you finished an hour ago that still shows unchecked tells them you are stuck on it.

If the plan turns out to be wrong, send the new plan as a full set of lines rather than patching around it.
