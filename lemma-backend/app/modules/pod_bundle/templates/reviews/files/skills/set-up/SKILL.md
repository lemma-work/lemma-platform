---
name: set-up
description: Set Reviews up from the repositories the team works in. Use once, in the chat Reviews was started in.
---

# Set Reviews up

Reviews owns "Every pull request is reviewed the same day", counted from `pull_requests`. That table starts empty, so the first job is to fill it from the team's repositories, keep it filling, and show the person where they stand. Do the steps in order and say what you did after each one, in a line.

1. **Ask where the code lives.** GitHub is the one this template reads. Check `lemma connectors list`; if GitHub is not connected, declare it with `lemma connectors pod-add` (see its `--help`) and give the person the `connect_url` that `lemma connectors run` answers with. Then ask with `ask_user` which repositories count, offering up to four of the busiest.
2. **Read the last four weeks.** List pull requests opened in the last four weeks in those repositories, and each one's first review, with `lemma connectors run github`.
3. **Leave out what isn't waiting on a person.** For each pull request, run `lemma decisions run` with its title, author and description as evidence and this schema:
   `{"type":"object","properties":{"needs_review":{"type":"boolean","description":"Does this pull request wait on a person's review? A bot's dependency bump or a draft does not."}}}`
   Leave out anything answered false or null.
4. **Write the rows.** One row per kept pull request in `pull_requests`: `title`, `repo`, `link`, `opened_at`, `reviewed_at` (its first review, if any), `reviewed_same_day` (true when that came the same working day), and `reviewer`. Write them all at once with `lemma records import pull_requests <file>`.
5. **Keep it true from now on.** List GitHub's triggers with `lemma connectors triggers list github` and create an automation on the one for a pull request opened or reviewed: `lemma schedules create --connector github --connector-trigger <trigger> --filter "Only a pull request that waits on a person's review." --instruction "Keep pull_requests true, following the keep-pull-requests skill."`.
6. **Show where they stand.** Count each of the four weeks with `lemma query run`, as the share reviewed the same day, and show it as a small widget with `display_resource`: one bar per week, with the bar Reviews will hold drawn across it.
7. **Ask for the bar.** Use `ask_user`: "Last four weeks, N% were reviewed the same day. What bar should Reviews hold every week?" Options: 90% (recommended), a step above where they are now, and where they are now. Set the answer with `set_what_you_own`, keeping the sentence, kind, unit and measure it has.
8. **Name the people.** Say who reviewed most, from `reviewer`, and that adding them to Reviews (⋯ › Add people) lets the nudges reach them. You cannot add people yourself.

Never comment on, approve or merge a pull request while setting up.
