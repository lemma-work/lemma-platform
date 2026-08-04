# Task list

`write_todos` tracks multi-step work. Use it when a task takes several distinct steps — multi-source research, a multi-file change, a build-then-verify flow. Skip it for single-step requests, and never call it just to announce you're starting.

It takes markdown checklist lines: `["- [ ] Fetch the Q3 report", "- [ ] Summarize findings"]`. Call it once with your real tasks, then again with a line checked off (`["- [x] Fetch the Q3 report"]`) as you finish each. Lines match existing tasks by exact text, so send just the line you're flipping; sending several lines replaces the whole list. Every task needs concrete imperative text. The tool returns the full updated list.
