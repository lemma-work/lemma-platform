---
name: set-up
description: Set Content up from where posts are drafted and published. Use once, in the chat Content was started in.
---

# Set Content up

Content owns "Two posts go out every week", counted from `posts`. That table starts empty, so the first job is to fill it from where the team drafts and publishes, keep it filling, and show the person where they stand. Do the steps in order and say what you did after each one, in a line.

1. **Ask where posts go out.** Use `ask_user` with one question and these options: LinkedIn, X, a blog in Notion, a newsletter in Mailchimp. Ask in a second question where drafts are kept, if somewhere else. Check `lemma connectors list` first and put anything already connected at the top.
2. **Connect it.** Declare each on this pod with `lemma connectors pod-add` (see its `--help`), then run any read of it with `lemma connectors run`: if it is not connected yet, the answer carries a `connect_url`. Give the person that link and wait until it works.
3. **Read the last four weeks.** List what was published, and what is in drafts, over four weeks with `lemma connectors run`.
4. **Keep only the team's posts.** For each item, run `lemma decisions run` with its title and opening as evidence and this schema:
   `{"type":"object","properties":{"is_post":{"type":"boolean","description":"Is this a post the team wrote for its audience? A reshare, a reply or an internal note is not."}}}`
   Leave out anything answered false or null.
5. **Write the rows.** One row per post in `posts`: `title`, `channel`, `link`, `status` (draft or published), `author`, and `published_at`. Write them all at once with `lemma records import posts <file>`.
6. **Keep it true from now on.** List the service's triggers with `lemma connectors triggers list <connector>`. If one fires on a new post or page, create an automation on it with `lemma schedules create --connector <connector> --connector-trigger <trigger> --instruction "Keep posts true, following the keep-posts skill."`. If there is none, create a daily one with `--cron "0 8 * * *"` that reads what is new since the last row.
7. **Show where they stand.** Count posts published in each of the four weeks with `lemma query run`, and show it as a small widget with `display_resource`: one bar per week, with the bar Content will hold drawn across it.
8. **Ask for the bar.** Use `ask_user`: "Last four weeks, N posts a week went out. How many should Content hold every week?" Options: 2 (recommended), one more than now, and where they are now. Set the answer with `set_what_you_own`, keeping the sentence, kind, unit and measure it has; rewrite the sentence when the number in it changes.
9. **Name the people.** Say who wrote most, from `author`, and that adding them to Content (⋯ › Add people) sends drafts to them to approve. You cannot add people yourself.

Never publish anything while setting up.
