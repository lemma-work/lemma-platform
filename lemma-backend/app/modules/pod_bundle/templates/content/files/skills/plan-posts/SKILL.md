---
name: plan-posts
description: Plan the week's posts and keep the posts table true to them. Use when asked to plan posts, when a planned time changes, or when a post goes out.
---

# Plan posts

Each post is one row in `posts`, added when it is planned, not when it goes out:

- `title`: what the post is, in a few words.
- `platform`: where it goes.
- `planned_for`: the time it should go out. Ask once for the team's usual posting times and keep them in memory.
- `sources`: links to what the post is built from.
- `status`: `planned`, then `drafted`, `posted` or `dropped`.

When a post goes out, set `posted_at` to the time it went live and `link` to the post itself. If a person changed the draft before it went out, set `approved_as_drafted` to false; if they posted it as written, true. Never move `planned_for` after the fact to make a late post look on time.
