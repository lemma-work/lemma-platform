---
name: keep-posts
description: Keep the posts table true. Use when a post is drafted, approved or published.
---

# Keep the posts table true

The number Content owns counts `posts` by `published_at` this week. Keep that column as it is.

- A draft exists: add a row with `title`, `author` and `status` "draft".
- It is approved: `status` "approved".
- It goes out: `status` "published", with `published_at`, where it went in `channel`, and `link`. Only published posts count.

Rows come from where the work happens, through the feed `lemma-setup` sets up. If nothing feeds this table yet, load `lemma-setup` before anything else.
