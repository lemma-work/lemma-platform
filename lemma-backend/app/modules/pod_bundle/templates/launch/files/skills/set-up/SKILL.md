---
name: set-up
description: Set Launch up with an owner and a date for every step. Use once, in the chat Launch was started in.
---

# Set Launch up

Launch owns shipping on its date, counted from `steps`, which already holds a checklist of common launch steps with nothing done. The number is real from the start; the first job is to make the checklist this team's, give every step an owner and a date, and connect where the work is tracked. Do the steps in order and say what you did after each one, in a line.

1. **Show the checklist.** Read `steps` with `lemma records list steps` and show it as a small widget with `display_resource`: each step, done or not, against the launch date.
2. **Ask where the work is tracked.** Use `ask_user` with one question and these options: Linear, Jira, GitHub, nowhere yet. Check `lemma connectors list` first and put anything already connected at the top.
3. **Connect it and read it.** If they named a tracker, declare it with `lemma connectors pod-add` (see its `--help`) and give the person the `connect_url` that `lemma connectors run` answers with. Then read the project or milestone for this launch.
4. **Match the tracker to the checklist.** For each tracker issue, run `lemma decisions run` with its title and description as evidence and this schema, listing the checklist's steps as the choices:
   `{"type":"object","properties":{"step":{"type":"string","enum":["<each step>","none"],"description":"Which launch step does this issue do?"},"done":{"type":"boolean","description":"Is it finished?"}}}`
   Mark a step done when its issues are, and add a step for work that matches none.
   Judge them from one shell loop, eight at a time with `--priority background` (`xargs -P 8` over one evidence file each), not one tool call each, and say how many there are before you start.
5. **Ask for owners and dates.** For the steps with no owner, use `ask_user` a few at a time, offering the people in this pod and "someone else". Set `owner` and `due_on` on each row with `lemma records update`.
6. **Keep it true from now on.** If the tracker has a trigger for an issue changing (`lemma connectors triggers list <connector>`), create an automation on it with `lemma schedules create --connector <connector> --connector-trigger <trigger> --instruction "Keep steps true, following the run-the-launch skill."`.
7. **Name the people.** Say who owns most steps, and that adding them to Launch (⋯ › Add people) sends the chases to them. You cannot add people yourself.

Never move the launch date yourself.
