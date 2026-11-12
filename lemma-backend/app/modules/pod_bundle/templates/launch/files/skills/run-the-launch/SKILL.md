---
name: run-the-launch
description: Keep the launch steps true. Use when a step is added, assigned, done, or slips.
---

# Run the launch

Every step to the launch is a row in `steps`, and the number Launch owns is the share that are `done`. Keep that column as it is.

- Give every step an `owner` and a `due_on` before the launch week. Ask when either is missing.
- Mark `done` only when the step is really done, and say so in the launch chat.
- A step past its date and not done is behind: ask its owner what is in the way, once a day.
- In a scheduled run, ask owners with `channel_post`: `ask_user` reaches only the person the run belongs to. Ask once per item: check `channel_read(asked_by_me=True)` before asking again.
