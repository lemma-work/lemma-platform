---
name: set-up
description: Set Support up from where customers already write. Use once, in the chat Support was started in.
---

# Set Support up

Support owns "Every customer hears back within a day", counted from `conversations`. That table starts empty, so the first job is to fill it from where customers already write, keep it filling, and show the person where they stand. Do the steps in order and say what you did after each one, in a line.

1. **Ask where customers write.** Use `ask_user` with one question and these options: Intercom, Zendesk, Freshdesk, a shared Gmail or Outlook inbox. Check `lemma connectors list` first and put anything already connected at the top.
2. **Connect it.** Declare it on this pod with `lemma connectors pod-add` (see its `--help`), then run any read of it with `lemma connectors run`: if it is not connected yet, the answer carries a `connect_url`. Give the person that link and wait until it works.
3. **Read the last four weeks.** Find the operation that lists conversations or threads with `lemma connectors operations search <connector> "list conversations"`, then page through four weeks with `lemma connectors run`.
4. **Keep only customers asking for something.** For each conversation, run `lemma decisions run` with the conversation's first message and subject as evidence and this schema:
   `{"type":"object","properties":{"is_customer_request":{"type":"boolean","description":"Is a customer asking the team for something?"}}}`
   Leave out anything answered false or null. An inbox is mostly receipts, notifications and newsletters; this is what keeps them out of the number.
5. **Write the rows.** One row per kept conversation in `conversations`: `customer`, `about` (a few words), `channel` (the service), `link`, `opened_at`, `answered_at` (the first reply a customer received, if any), `answered_in_time` (true when that was within a day of `opened_at`), and `with_person` (the teammate who replied). Write them all at once with `lemma records import conversations <file>`.
6. **Keep it true from now on.** List the service's triggers with `lemma connectors triggers list <connector>`. If one fires on a new conversation, create an automation on it with `lemma schedules create --connector <connector> --connector-trigger <trigger> --filter "Only a customer asking the team for something." --instruction "Add the conversation to conversations, following the keep-conversations skill."`. If the service has no such trigger, create an hourly one with `--cron "0 * * * *"` that reads what is new since the last row.
7. **Show where they stand.** Count each of the four weeks with `lemma query run`, as the share of conversations answered within a day, and show it as a small widget with `display_resource`: one bar per week, with the bar Support will hold drawn across it.
8. **Ask for the bar.** Use `ask_user`: "Last four weeks, N% heard back within a day. What bar should Support hold every week?" Options: 90% (recommended), a step above where they are now, and where they are now. Set the answer with `set_what_you_own`, keeping the sentence, kind, unit and measure it has.
9. **Name the people.** Say who answered most of these, from `with_person`, and that adding them to Support (⋯ › Add people) sends each morning's drafts to whoever answered that customer before. You cannot add people yourself.

Never send anything to a customer while setting up.
