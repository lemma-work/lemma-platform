---
name: set-up
description: Set Follow-ups up from where leads already arrive. Use once, in the chat Follow-ups was started in.
---

# Set Follow-ups up

Follow-ups owns "Every lead gets a follow-up within two days", counted from `leads`. That table starts empty, so the first job is to fill it from where leads already arrive, keep it filling, and show the person where they stand. Do the steps in order and say what you did after each one, in a line. `lemma-setup` holds the rules behind each step; this skill is what is particular to Follow-ups.

1. **Ask where leads come in.** One `ask_user` with two questions: where leads come in (HubSpot, Salesforce, a shared Gmail or Outlook inbox), and which city's time the team works in. Check `lemma connectors list` first and put anything already connected at the top. Then set each schedule in `lemma schedules list` to that timezone, keeping its cron: `lemma schedules update <name> -d '{"config":{"cron":"<its cron>","timezone":"<zone>"}}'`.
2. **Connect it.** Find the read with `lemma connectors run <connector> "list leads" --dry-run`, then run one page of it for real: that proves the connection. If it is not connected yet, the answer carries a `connect_url`: give the person that link as it is, end your turn, and run the same call again when they say it is done. If the service is not in the catalog, say so and ask how the work can reach you instead (`lemma-setup`, step 2); an export they upload is history only.
3. **Read the last four weeks.** Page through four weeks of new contacts, leads or threads, newest first and at most 200, saving them to a workspace file rather than carrying them in your context. Write down when this read started, and say how many older ones were left.
4. **Keep only real leads.** For each one, run `lemma decisions run` with what they sent or what the record says as evidence and this schema:
   `{"type":"object","properties":{"is_lead":{"type":"boolean","description":"Is this a person or company who might buy, waiting to hear from us?"}}}`
   Leave out anything answered false or null.
   Judge them from one shell loop, eight at a time with `--priority background` (`xargs -P 8` over one evidence file each), not one tool call each, and say how many there are before you start. Write each verdict to a file as it comes back, so a stopped loop picks up where it left off; a refused call gets one more try, then counts as left out.
5. **Write the rows.** One row per lead in `leads`: `name`, `company`, `link` (the record's or thread's own URL, or its id at the service: this is what stops it being added twice), `came_in_at`, `followed_up_at` (the first reply they received, if any), `followed_up_in_time` (true when that was within two days of `came_in_at`), and `owner` (who followed up, or the record's owner). Write them all at once with `lemma records import leads <file>`, and check the count with `lemma query run`.
6. **Keep it true from now on.** A row has to arrive when a lead comes in and change when they hear back. List the service's triggers with `lemma connectors triggers list <connector>`, and create one automation for a new contact or deal and one for a reply or logged email to one, each like this:
   `lemma schedules create --name new-leads --agent POD_DEFAULT --webhook-source composio --connector <connector> --connector-trigger <trigger> --filter "Only a new lead, or a reply to one." --instruction "Add the lead to leads, or update its row when its link is already there, following the keep-leads skill."`
   Then read the service once more for anything since the step 3 read started, skipping links already in the table. `link` is unique: a write refused for a duplicate link means the row is already there, so skip it. When there is no trigger for one of the two, create an hourly automation for it instead (`--agent POD_DEFAULT --cron "0 * * * *"`, same instruction) that reads only what is new or answered since its last run, and tell the person the number can be up to an hour behind.
7. **Show where they stand.** Count each of the four weeks with `lemma query run` the way the measure does: each lead in the week its two days ran out, counting only those followed up or past their two days. Show it as a small widget with `display_resource`: one bar per week, with the bar Follow-ups will hold drawn across it. A week with nothing due shows no bar, never a 0.
8. **Ask for the bar.** Use `ask_user`: "Last four weeks, N% of leads heard from you within two days. What bar should Follow-ups hold every week?" Each option is a number: 95% (recommended), N plus 5 rounded to the nearest 5, and N. Drop an option that repeats another. Set the answer with `set_what_you_own`, keeping the sentence, kind, unit and measure it has.
9. **Name the people.** Say who followed up most, from `owner`, and that adding them to Follow-ups (⋯ › Add people) sends each morning's drafts to the lead's owner. You cannot add people yourself.

Then fill in `/plan.md` and finish as `lemma-setup` says (steps 8 and Finish).

Never send anything to a lead while setting up.
