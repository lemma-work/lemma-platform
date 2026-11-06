---
name: keep-posts
description: Keep the posts table true. Use when a post is drafted, approved or published.
---

# Keep the posts table true

- A draft exists: add a row with `title`, `author` and `status` "draft".
- It is approved: `status` "approved".
- It goes out: `status` "published", with `published_at`, `channel` and `link`. Only published posts count.
