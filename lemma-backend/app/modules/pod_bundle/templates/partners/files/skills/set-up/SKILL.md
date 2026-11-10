---
name: set-up
description: Set Partners up from where the conversations with companies are kept. Use once, in the chat Partners was started in.
---

# Set Partners up

Partners owns a number of design partners signed by a date, counted from `partners`. That table starts empty, so the first job is to fill it from where the team keeps its conversations with companies, keep it filling, and show the person where they stand. Do the steps in order and say what you did after each one, in a line. `lemma-setup` holds the rules behind each step; this skill is what is particular to Partners.

1. **Ask where the conversations are kept.** One `ask_user` with two questions: where the conversations are kept (HubSpot, Salesforce, a shared Gmail or Outlook inbox), and which city's time the team works in. Check `lemma connectors list` first and put anything already connected at the top. Then set each schedule in `lemma schedules list` to that timezone, keeping its cron: `lemma schedules update <name> -d '{"config":{"cron":"<its cron>","timezone":"<zone>"}}'`.
2. **Connect it.** Find the read with `lemma connectors run <connector> "list deals" --dry-run`, then run one page of it for real: that proves the connection. If it is not connected yet, the answer carries a `connect_url`: give the person that link as it is, end your turn, and run the same call again when they say it is done. If the service is not in the catalog, say so and ask how the work can reach you instead (`lemma-setup`, step 2); an export they upload is history only.
3. **Read what is open.** List the companies or deals the team is talking to, at most 200 and the most recently active first, with the last two months of threads with them, saving them to a workspace file rather than carrying them in your context. Write down when this read started, and say how many were left.
4. **Keep only design-partner conversations.** For each company, run `lemma decisions run` with what the record and the latest thread say as evidence and this schema:
   `{"type":"object","properties":{"is_partner_prospect":{"type":"boolean","description":"Is this a company we are asking to become a design partner?"},"stage":{"type":"string","enum":["talking","trial","signing","signed"],"description":"Where does it stand?"}}}`
   Leave out anything answered false or null on the first question.
   Judge them from one shell loop, eight at a time with `--priority background` (`xargs -P 8` over one evidence file each), not one tool call each, and say how many there are before you start. Write each verdict to a file as it comes back, so a stopped loop picks up where it left off; a refused call gets one more try, then counts as left out.
5. **Write the rows.** One row per company in `partners`: `company`, `link` (the company's or deal's own URL, or its id at the service: this is what stops it being added twice), `owner`, `stage`, `next_step`, `next_step_due`, and `signed`. Write them all at once with `lemma records import partners <file>`, and check the count with `lemma query run`.
6. **Keep it true from now on.** List the service's triggers with `lemma connectors triggers list <connector>`. If one fires when a deal or thread changes, create an automation on it:
   `lemma schedules create --name partner-changes --agent POD_DEFAULT --webhook-source composio --connector <connector> --connector-trigger <trigger> --instruction "Keep partners true, following the keep-partners skill: update the company's row when its link is already there, and add one only for a company we are asking to become a design partner."`
   Then read the service once more for anything since the step 3 read started. `link` is unique: a write refused for a duplicate link means the row is already there, so update it instead. When there is no such trigger, create a daily automation instead (`--agent POD_DEFAULT --cron "0 8 * * 1-5"`, same instruction) that reads only what changed since its last run, and tell the person the count can be up to a day behind.
7. **Show where they stand.** Count signed partners and the ones in signing with `lemma query run`, and show it as a small widget with `display_resource` against the target and the date.
8. **Check the target.** Use `ask_user`: "N signed, M close. Keep the target at 20 by the date?" Each option is a concrete value: keep 20 by the date (recommended), N plus M by the date, and 20 by a date a month later. Set a change with `set_what_you_own`, keeping the measure it has and rewriting the sentence to match.
9. **Name the people.** Say who owns the most conversations, from `owner`, and that adding them to Partners (⋯ › Add people) sends the chases to them. You cannot add people yourself.

Then fill in `/plan.md` and finish as `lemma-setup` says (steps 8 and Finish).

Never write to a company while setting up.
