---
name: set-up
description: Set Audit up with an owner and evidence for every control. Use once, in the chat Audit was started in.
---

# Set Audit up

Audit owns passing on its date, counted from `controls`, which already holds a list of common controls with none ready. The number is real from the start; the first job is to make the list this team's, find the evidence that already exists, and give every control an owner. Do the steps in order and say what you did after each one, in a line. `lemma-setup` holds the rules behind each step; this skill is what is particular to Audit.

1. **Show the controls.** Read `controls` with `lemma records list controls` and show it as a small widget with `display_resource`: each control, ready or not, against the audit date.
2. **Ask where evidence is kept.** One `ask_user` with two questions: where evidence is kept (Notion, Google Drive, Confluence, nowhere yet), and which city's time the team works in. Check `lemma connectors list` first and put anything already connected at the top. Then set each schedule in `lemma schedules list` to that timezone, keeping its cron: `lemma schedules update <name> -d '{"config":{"cron":"<its cron>","timezone":"<zone>"}}'`.
3. **Connect it and read it.** If they named a place, find the read with `lemma connectors run <connector> "list documents" --dry-run`, then run one page of it for real: that proves the connection. If it is not connected yet, the answer carries a `connect_url`: give the person that link as it is, end your turn, and run the same call again when they say it is done. Then list the policies and records kept there, at most 200, saving them to a workspace file rather than carrying them in your context. If they said nowhere yet, skip to step 5: evidence comes in when owners send it.
4. **Match evidence to controls.** For each document, run `lemma decisions run` with its title and opening as evidence and this schema, listing the controls as the choices:
   `{"type":"object","properties":{"control":{"type":"string","enum":["<each control>","none"],"description":"Which control is this evidence for?"},"current":{"type":"boolean","description":"Is it dated within the last 90 days?"}}}`
   On each matched control set `evidence_link`, and `evidence_on` to the document's own date; set `ready` when it is current.
   Judge them from one shell loop, eight at a time with `--priority background` (`xargs -P 8` over one evidence file each), not one tool call each, and say how many there are before you start. Write each verdict to a file as it comes back, so a stopped loop picks up where it left off; a refused call gets one more try, then counts as left out.
5. **Ask for owners.** For the controls with no owner, use `ask_user` a few at a time, offering the people in this pod and "someone else". Set `owner` and `due_on` on each row with `lemma records update`.
6. **Keep it true from now on.** If the place has a trigger for a document added or changed (`lemma connectors triggers list <connector>`), create an automation on it:
   `lemma schedules create --name new-evidence --agent POD_DEFAULT --webhook-source composio --connector <connector> --connector-trigger <trigger> --instruction "Match this document to a control, following the keep-controls skill."`
   When there is none, create a daily automation instead (`--agent POD_DEFAULT --cron "0 8 * * 1-5"`, same instruction) that reads only the documents changed since its last run, and tell the person new evidence can show a day late.
7. **Name the people.** Say who owns most controls, and that adding them to Audit (⋯ › Add people) sends the Monday checks to them. You cannot add people yourself.

Then fill in `/plan.md` and finish as `lemma-setup` says (steps 8 and Finish).

Never mark a control ready without evidence that names it.
