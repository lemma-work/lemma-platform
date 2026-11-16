---
name: set-up
description: Set Content up from where posts are drafted and published. Use once, in the chat Content was started in.
---

# Set Content up

Content keeps its work in `posts`. That table starts empty, so the first job is to fill it from where the team drafts and publishes and keep it filling. Do the steps in order and say what you did after each one, in a line. `lemma-setup` holds the rules behind each step; this skill is what is particular to Content.

1. **Ask where posts go out.** One `ask_user` with three questions: where posts go out (LinkedIn, X, a blog in Notion, a newsletter in Mailchimp), where drafts are kept if somewhere else, and which city's time the team works in. Check `lemma connectors list` first and put anything already connected at the top. Then set each schedule in `lemma schedules list` to that timezone, keeping its cron: `lemma schedules update <name> -d '{"config":{"cron":"<its cron>","timezone":"<zone>"}}'`.
2. **Connect it.** For each place, find the read with `lemma connectors run <connector> "list posts" --dry-run`, then run one page of it for real: that proves the connection. If it is not connected yet, the answer carries a `connect_url`: give the person that link as it is, end your turn, and run the same call again when they say it is done. If a service is not in the catalog, say so and ask how the work can reach you instead (`lemma-setup`, step 2); an export they upload is history only.
3. **Read the last four weeks.** List what was published, and what is in drafts, over four weeks, newest first and at most 200 items, saving them to a workspace file rather than carrying them in your context. Write down when this read started, and say how many older ones were left.
4. **Keep only the team's posts.** For each item, run `lemma decisions run` with its title and opening as evidence and this schema:
   `{"type":"object","properties":{"is_post":{"type":"boolean","description":"Is this a post the team wrote for its audience? A reshare, a reply or an internal note is not."}}}`
   Leave out anything answered false or null.
   Judge them from one shell loop, eight at a time with `--priority background` (`xargs -P 8` over one evidence file each), not one tool call each, and say how many there are before you start. Write each verdict to a file as it comes back, so a stopped loop picks up where it left off; a refused call gets one more try, then counts as left out.
5. **Write the rows.** One row per post in `posts`: `title`, `channel`, `link` (the post's or draft's own URL, or its id at the service: this is what stops it being added twice), `status` (draft or published), `author`, and `published_at`. Write them all at once with `lemma records import posts <file>`, and check the count with `lemma query run`.
6. **Keep it true from now on.** A row has to arrive when a draft is started and change when it is published. List the service's triggers with `lemma connectors triggers list <connector>`. If one fires on a new or published post or page, create an automation on it:
   `lemma schedules create --name new-posts --agent POD_DEFAULT --webhook-source composio --connector <connector> --connector-trigger <trigger> --instruction "Add the post to posts, or update its row when its link is already there, following the keep-posts skill."`
   Then read the service once more for anything since the step 3 read started, skipping links already in the table. `link` is unique: a write refused for a duplicate link means the row is already there, so skip it. When there is no such trigger, create a daily automation instead (`--agent POD_DEFAULT --cron "0 8 * * *"`, same instruction) that reads only what is new or published since its last run, and tell the person the count can be up to a day behind.
7. **Name the people.** Say who wrote most, from `author`, and that adding them to Content (⋯ › Add people) sends drafts to them to approve. You cannot add people yourself.

Then keep what you were taught in memory and finish as `lemma-setup` says (step 6 and Finish).

Never publish anything while setting up.
