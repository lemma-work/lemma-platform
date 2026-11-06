---
name: set-up
description: Set Partners up from where the conversations with companies are kept. Use once, in the chat Partners was started in.
---

# Set Partners up

Partners owns a number of design partners signed by a date, counted from `partners`. That table starts empty, so the first job is to fill it from where the team keeps its conversations with companies, keep it filling, and show the person where they stand. Do the steps in order and say what you did after each one, in a line.

1. **Ask where the conversations are kept.** Use `ask_user` with one question and these options: HubSpot, Salesforce, a shared Gmail or Outlook inbox. Check `lemma connectors list` first and put anything already connected at the top.
2. **Connect it.** Declare it on this pod with `lemma connectors pod-add` (see its `--help`), then run any read of it with `lemma connectors run`: if it is not connected yet, the answer carries a `connect_url`. Give the person that link and wait until it works.
3. **Read what is open.** List the companies or deals the team is talking to, and the last two months of threads with them, with `lemma connectors run`.
4. **Keep only design-partner conversations.** For each company, run `lemma decisions run` with what the record and the latest thread say as evidence and this schema:
   `{"type":"object","properties":{"is_partner_prospect":{"type":"boolean","description":"Is this a company we are asking to become a design partner?"},"stage":{"type":"string","enum":["talking","trial","signing","signed"],"description":"Where does it stand?"}}}`
   Leave out anything answered false or null on the first question.
5. **Write the rows.** One row per company in `partners`: `company`, `owner`, `stage`, `next_step`, `next_step_due`, and `signed`. Write them all at once with `lemma records import partners <file>`.
6. **Keep it true from now on.** List the service's triggers with `lemma connectors triggers list <connector>`. If one fires when a deal or thread changes, create an automation on it with `lemma schedules create --connector <connector> --connector-trigger <trigger> --instruction "Keep partners true, following the keep-partners skill."`. If there is none, create a daily one with `--cron "0 8 * * 1-5"`.
7. **Show where they stand.** Count signed partners and the ones in signing with `lemma query run`, and show it as a small widget with `display_resource` against the target and the date.
8. **Check the target.** Use `ask_user`: "N signed, M close. Keep the target at 20 by the date?" Options: keep it (recommended), lower it, move the date. Set a change with `set_what_you_own`, keeping the measure it has.
9. **Name the people.** Say who owns the most conversations, from `owner`, and that adding them to Partners (⋯ › Add people) sends the chases to them. You cannot add people yourself.

Never write to a company while setting up.
