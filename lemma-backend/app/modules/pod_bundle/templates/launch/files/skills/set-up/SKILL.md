---
name: set-up
description: Set Launch up with an owner and a date for every step. Use once, in the chat Launch was started in.
---

# Set Launch up

Launch owns shipping on its date, counted from `steps`, which already holds a checklist of common launch steps with nothing done. The number is real from the start; the first job is to make the checklist this team's, give every step an owner and a date, and connect where the work is tracked. Do the steps in order and say what you did after each one, in a line. `lemma-setup` holds the rules behind each step; this skill is what is particular to Launch.

1. **Show the checklist.** Read `steps` with `lemma records list steps` and show it as a small widget with `display_resource`: each step, done or not, against the launch date.
2. **Ask where the work is tracked.** One `ask_user` with two questions: where the work is tracked (Linear, Jira, GitHub, nowhere yet), and which city's time the team works in. Check `lemma connectors list` first and put anything already connected at the top. Then set each schedule in `lemma schedules list` to that timezone, keeping its cron: `lemma schedules update <name> -d '{"config":{"cron":"<its cron>","timezone":"<zone>"}}'`.
3. **Connect it and read it.** If they named a tracker, find the read with `lemma connectors run <connector> "list issues" --dry-run`, then run one page of it for real: that proves the connection. If it is not connected yet, the answer carries a `connect_url`: give the person that link as it is, end your turn, and run the same call again when they say it is done. Then read the project or milestone for this launch, at most 200 issues, saving them to a workspace file rather than carrying them in your context. If they said nowhere yet, skip to step 5: the steps change when people tell you.
4. **Match the tracker to the checklist.** For each tracker issue, run `lemma decisions run` with its title and description as evidence and this schema, listing the checklist's steps as the choices:
   `{"type":"object","properties":{"step":{"type":"string","enum":["<each step>","none"],"description":"Which launch step does this issue do?"},"done":{"type":"boolean","description":"Is it finished?"}}}`
   Mark a step done when its issues are, and add a step for work that matches none. Put each matched issue's link in the step's `notes`.
   Judge them from one shell loop, eight at a time with `--priority background` (`xargs -P 8` over one evidence file each), not one tool call each, and say how many there are before you start. Write each verdict to a file as it comes back, so a stopped loop picks up where it left off; a refused call gets one more try, then counts as left out.
5. **Ask for owners and dates.** For the steps with no owner, use `ask_user` a few at a time, offering the people in this pod and "someone else". Set `owner` and `due_on` on each row with `lemma records update`.
6. **Keep it true from now on.** If the tracker has a trigger for an issue changing (`lemma connectors triggers list <connector>`), create an automation on it:
   `lemma schedules create --name tracker-changes --agent POD_DEFAULT --webhook-source composio --connector <connector> --connector-trigger <trigger> --instruction "Keep steps true, following the run-the-launch skill."`
   When there is none, create a daily automation instead (`--agent POD_DEFAULT --cron "0 8 * * 1-5"`, same instruction) that reads only the issues changed since its last run, and tell the person a finished step can show a day late.
7. **Name the people.** Say who owns most steps, and that adding them to Launch (⋯ › Add people) sends the chases to them. You cannot add people yourself.

Then fill in `/plan.md` and finish as `lemma-setup` says (steps 8 and Finish).

Never move the launch date yourself.
