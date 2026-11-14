---
name: set-up
description: Set Support up from where customers already write. Use once, in the chat Support was started in.
---

# Set Support up

Support keeps its work in `conversations`. That table starts empty, so the first job is to fill it from where customers already write and keep it filling. Do the steps in order and say what you did after each one, in a line. `lemma-setup` holds the rules behind each step; this skill is what is particular to Support.

1. **Ask where customers write.** One `ask_user` with two questions: where customers write (Intercom, Zendesk, Freshdesk, a shared Gmail or Outlook inbox), and which city's time the team works in. Check `lemma connectors list` first and put anything already connected at the top. Then set each schedule in `lemma schedules list` to that timezone, keeping its cron: `lemma schedules update <name> -d '{"config":{"cron":"<its cron>","timezone":"<zone>"}}'`.
2. **Connect it.** Find the read with `lemma connectors run <connector> "list conversations" --dry-run`, then run one page of it for real: that proves the connection. If it is not connected yet, the answer carries a `connect_url`: give the person that link as it is, end your turn, and run the same call again when they say it is done. If the service is not in the catalog, say so and ask how the work can reach you instead (`lemma-setup`, step 2); an export they upload is history only.
3. **Read the last four weeks.** Page through four weeks, newest first and at most 200 conversations, saving them to a workspace file rather than carrying them in your context. Write down when this read started, and say how many older ones were left.
4. **Keep only customers asking for something.** Skip this for Intercom, Zendesk and Freshdesk, where every conversation is a customer writing in. For an inbox, run, for each conversation, `lemma decisions run` with the conversation's first message and subject as evidence and this schema:
   `{"type":"object","properties":{"is_customer_request":{"type":"boolean","description":"Is a customer asking the team for something?"}}}`
   Leave out anything answered false or null. An inbox is mostly receipts, notifications and newsletters; this is what keeps them out of the table.
   Judge them from one shell loop, eight at a time with `--priority background` (`xargs -P 8` over one evidence file each), not one tool call each, and say how many there are before you start. Write each verdict to a file as it comes back, so a stopped loop picks up where it left off; a refused call gets one more try, then counts as left out.
5. **Write the rows.** One row per kept conversation in `conversations`: `customer`, `about` (a few words), `channel` (the service), `link` (the conversation's own URL, or its id at the service: this is what stops it being added twice), `opened_at`, `answered_at` (the first reply a customer received, if any), `answered_in_time` (true when that was within a day of `opened_at`), and `with_person` (the teammate who replied). Write them all at once with `lemma records import conversations <file>`, and check the count with `lemma query run`.
6. **Keep it true from now on.** A row has to arrive when a customer writes and change when they are answered. List the service's triggers with `lemma connectors triggers list <connector>`, and create one automation for a new conversation and one for a reply, each like this:
   `lemma schedules create --name new-conversations --agent POD_DEFAULT --webhook-source composio --connector <connector> --connector-trigger <trigger> --filter "Only a customer asking the team for something, or a reply to one." --instruction "Add the conversation to conversations, or update its row when its link is already there, following the keep-conversations skill."`
   Then read the service once more for anything since the step 3 read started, skipping links already in the table. `link` is unique: a write refused for a duplicate link means the row is already there, so skip it. When there is no trigger for one of the two, create an hourly automation for it instead (`--agent POD_DEFAULT --cron "0 * * * *"`, same instruction) that reads only what is new or answered since its last run, and tell the person the table can be up to an hour behind.
7. **Name the people.** Say who answered most of these, from `with_person`, and that adding them to Support (⋯ › Add people) sends each morning's drafts to whoever answered that customer before. You cannot add people yourself.

Then keep what you were taught in memory and finish as `lemma-setup` says (step 6 and Finish).

Never send anything to a customer while setting up.
