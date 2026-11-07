---
name: set-up
description: Set Audit up with an owner and evidence for every control. Use once, in the chat Audit was started in.
---

# Set Audit up

Audit owns passing on its date, counted from `controls`, which already holds a list of common controls with none ready. The number is real from the start; the first job is to make the list this team's, find the evidence that already exists, and give every control an owner. Do the steps in order and say what you did after each one, in a line.

1. **Show the controls.** Read `controls` with `lemma records list controls` and show it as a small widget with `display_resource`: each control, ready or not, against the audit date.
2. **Ask where evidence is kept.** Use `ask_user` with one question and these options: Notion, Google Drive, Confluence, nowhere yet. Check `lemma connectors list` first and put anything already connected at the top.
3. **Connect it and read it.** If they named a place, declare it with `lemma connectors pod-add` (see its `--help`) and give the person the `connect_url` that `lemma connectors run` answers with. Then list the policies and records kept there.
4. **Match evidence to controls.** For each document, run `lemma decisions run` with its title and opening as evidence and this schema, listing the controls as the choices:
   `{"type":"object","properties":{"control":{"type":"string","enum":["<each control>","none"],"description":"Which control is this evidence for?"},"current":{"type":"boolean","description":"Is it dated within the last 90 days?"}}}`
   Set `evidence_link` on each matched control, and `ready` when the evidence is current.
   Judge them from one shell loop, eight at a time with `--priority background` (`xargs -P 8` over one evidence file each), not one tool call each, and say how many there are before you start.
5. **Ask for owners.** For the controls with no owner, use `ask_user` a few at a time, offering the people in this pod and "someone else". Set `owner` and `due_on` on each row with `lemma records update`.
6. **Name the people.** Say who owns most controls, and that adding them to Audit (⋯ › Add people) sends the Monday checks to them. You cannot add people yourself.

Never mark a control ready without evidence that names it.
