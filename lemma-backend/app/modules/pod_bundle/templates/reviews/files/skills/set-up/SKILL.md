---
name: set-up
description: Set Reviews up from the repositories the team works in. Use once, in the chat Reviews was started in.
---

# Set Reviews up

Reviews owns "Every pull request is reviewed the same day", counted from `pull_requests`. That table starts empty, so the first job is to fill it from the team's repositories, keep it filling, and show the person where they stand. Do the steps in order and say what you did after each one, in a line. `lemma-setup` holds the rules behind each step; this skill is what is particular to Reviews.

1. **Connect GitHub.** GitHub is the one this template reads. Find the read with `lemma connectors run github "list pull requests" --dry-run`, then run one page of it for real: that proves the connection. If it is not connected yet, the answer carries a `connect_url`: give the person that link as it is, end your turn, and run the same call again when they say it is done.
2. **Ask which repositories count.** One `ask_user` with two questions: which repositories count, offering up to four of the busiest, and which city's time the team works in, since "the same day" is the team's day. Then set each schedule in `lemma schedules list` to that timezone, keeping its cron: `lemma schedules update <name> -d '{"config":{"cron":"<its cron>","timezone":"<zone>"}}'`.
3. **Read the last four weeks.** List pull requests opened in those repositories in the last four weeks, newest first and at most 200, with each one's first review, saving them to a workspace file rather than carrying them in your context. Write down when this read started, and say how many older ones were left.
4. **Leave out what isn't waiting on a person.** For each pull request, run `lemma decisions run` with its title, author and description as evidence and this schema:
   `{"type":"object","properties":{"needs_review":{"type":"boolean","description":"Does this pull request wait on a person's review? A bot's dependency bump or a draft does not."}}}`
   Leave out anything answered false or null. A rule settles some without asking: an author that is always a bot, a pull request marked draft.
   Judge the rest from one shell loop, eight at a time with `--priority background` (`xargs -P 8` over one evidence file each), not one tool call each, and say how many there are before you start. Write each verdict to a file as it comes back, so a stopped loop picks up where it left off; a refused call gets one more try, then counts as left out.
5. **Write the rows.** One row per kept pull request in `pull_requests`: `title`, `repo`, `link` (its URL: this is what stops it being added twice), `opened_at`, `reviewed_at` (its first review, if any), `reviewed_same_day` (true when that came the same working day), and `reviewer`. Write them all at once with `lemma records import pull_requests <file>`, and check the count with `lemma query run`.
6. **Keep it true from now on.** A row has to arrive when a pull request opens and change when it is reviewed. List GitHub's triggers with `lemma connectors triggers list github`, and create one automation for a pull request opened and one for a review submitted, each like this:
   `lemma schedules create --name new-pull-requests --agent POD_DEFAULT --webhook-source composio --connector github --connector-trigger <trigger> --filter "Only a pull request that waits on a person's review, or a review of one." --instruction "Add the pull request to pull_requests, or update its row when its link is already there, following the keep-pull-requests skill."`
   Then read the repositories once more for anything since the step 3 read started, skipping links already in the table. `link` is unique: a write refused for a duplicate link means the row is already there, so skip it.
7. **Show where they stand.** Count each of the four weeks with `lemma query run` the way the measure does: each pull request in the week it opened, counting only those reviewed or opened before today. Show it as a small widget with `display_resource`: one bar per week, with the bar Reviews will hold drawn across it. A week with nothing due shows no bar, never a 0.
8. **Ask for the bar.** Use `ask_user`: "Last four weeks, N% were reviewed the same day. What bar should Reviews hold every week?" Each option is a number: 90% (recommended), N plus 5 rounded to the nearest 5, and N. Drop an option that repeats another. Set the answer with `set_what_you_own`, keeping the sentence, kind, unit and measure it has.
9. **Name the people.** Say who reviewed most, from `reviewer`, and that adding them to Reviews (⋯ › Add people) lets the nudges reach them. You cannot add people yourself.

Then fill in `/plan.md` and finish as `lemma-setup` says (steps 8 and Finish).

Never comment on, approve or merge a pull request while setting up.
