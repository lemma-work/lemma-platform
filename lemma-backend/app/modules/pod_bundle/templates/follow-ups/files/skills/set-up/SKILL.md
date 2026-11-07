---
name: set-up
description: Set Follow-ups up from where leads already arrive. Use once, in the chat Follow-ups was started in.
---

# Set Follow-ups up

Follow-ups owns "Every lead gets a follow-up within two days", counted from `leads`. That table starts empty, so the first job is to fill it from where leads already arrive, keep it filling, and show the person where they stand. Do the steps in order and say what you did after each one, in a line.

1. **Ask where leads come in.** Use `ask_user` with one question and these options: HubSpot, Salesforce, a shared Gmail or Outlook inbox. Check `lemma connectors list` first and put anything already connected at the top.
2. **Connect it.** Declare it on this pod with `lemma connectors pod-add` (see its `--help`), then run any read of it with `lemma connectors run`: if it is not connected yet, the answer carries a `connect_url`. Give the person that link and wait until it works.
3. **Read the last four weeks.** Find the operation that lists new contacts, leads or threads with `lemma connectors operations search <connector> "list leads"`, then page through four weeks with `lemma connectors run`.
4. **Keep only real leads.** For each one, run `lemma decisions run` with what they sent or what the record says as evidence and this schema:
   `{"type":"object","properties":{"is_lead":{"type":"boolean","description":"Is this a person or company who might buy, waiting to hear from us?"}}}`
   Leave out anything answered false or null.
   Judge them from one shell loop, eight at a time with `--priority background` (`xargs -P 8` over one evidence file each), not one tool call each, and say how many there are before you start.
5. **Write the rows.** One row per lead in `leads`: `name`, `company`, `link`, `came_in_at`, `followed_up_at` (the first reply they received, if any), `followed_up_in_time` (true when that was within two days of `came_in_at`), and `owner` (who followed up, or the record's owner). Write them all at once with `lemma records import leads <file>`.
6. **Keep it true from now on.** List the service's triggers with `lemma connectors triggers list <connector>`. If one fires on a new contact or deal, create an automation on it with `lemma schedules create --connector <connector> --connector-trigger <trigger> --filter "Only a new lead." --instruction "Add the lead to leads, following the keep-leads skill."`. If there is no such trigger, create an hourly one with `--cron "0 * * * *"` that reads what is new since the last row.
7. **Show where they stand.** Count each of the four weeks with `lemma query run`, as the share of leads followed up within two days, and show it as a small widget with `display_resource`: one bar per week, with the bar Follow-ups will hold drawn across it.
8. **Ask for the bar.** Use `ask_user`: "Last four weeks, N% of leads heard from you within two days. What bar should Follow-ups hold every week?" Options: 95% (recommended), a step above where they are now, and where they are now. Set the answer with `set_what_you_own`, keeping the sentence, kind, unit and measure it has.
9. **Name the people.** Say who followed up most, from `owner`, and that adding them to Follow-ups (⋯ › Add people) sends each morning's drafts to the lead's owner. You cannot add people yourself.

Never send anything to a lead while setting up.
