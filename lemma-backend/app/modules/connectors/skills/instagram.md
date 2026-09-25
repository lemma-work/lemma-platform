# Instagram

Instagram is a social media platform for sharing photos, videos, and stories. It requires a Business or Creator account; personal accounts are not supported.

**Auth config name:** `instagram`

## Common Tasks

### Retrieve recent media
Use to list the latest posts, reels, and carousels from your connected account with key details.
```
lemma connectors operations execute instagram INSTAGRAM_GET_IG_USER_MEDIA -d '{"payload": {"ig_user_id": "me", "limit": 10, "fields": "id,caption,media_url,like_count,timestamp"}}'
```

### Get media insights
Use to pull performance metrics like reach and impressions for a specific post.
```
lemma connectors operations execute instagram INSTAGRAM_GET_IG_MEDIA_INSIGHTS -d '{"payload": {"ig_media_id": "17858625910184312", "metric": ["reach", "impressions"], "period": "lifetime"}}'
```

### Fetch comments on a post
Use to retrieve recent comments on a media item for moderation or engagement analysis.
```
lemma connectors operations execute instagram INSTAGRAM_GET_IG_MEDIA_COMMENTS -d '{"payload": {"ig_media_id": "17858625910184312", "limit": 50, "fields": "id,text,username,timestamp,like_count"}}'
```

### Get comment replies
Use to view threaded replies under a parent comment for deeper conversation tracking.
```
lemma connectors operations execute instagram INSTAGRAM_GET_IG_COMMENT_REPLIES -d '{"payload": {"ig_comment_id": "17912345678901234", "limit": 25, "fields": "id,text,username,timestamp"}}'
```

### Delete a comment you posted
Use to remove a comment your account created, for example when cleaning up an accidental reply.
```
lemma connectors operations execute instagram INSTAGRAM_DELETE_COMMENT -d '{"payload": {"ig_comment_id": "17912345678901234"}}'
```

### Create a carousel draft
Use to prepare a multi-image post before publishing; upload images via public URLs and add a caption.
```
lemma connectors operations execute instagram INSTAGRAM_CREATE_CAROUSEL_CONTAINER -d '{"payload": {"ig_user_id": "17841400008460056", "child_image_urls": ["https://images.pexels.com/photos/12345/pexels-photo-12345.jpeg", "https://images.pexels.com/photos/67890/pexels-photo-67890.jpeg"], "caption": "Weekend vibes at the boardwalk 🎡 #weekendfun", "graph_api_version": "v21.0"}}'
```

### Check publishing quota
Use to monitor your remaining daily post limit before publishing to avoid hitting the quota.
```
lemma connectors operations execute instagram INSTAGRAM_GET_IG_USER_CONTENT_PUBLISHING_LIMIT -d '{"payload": {"ig_user_id": "me"}}'
```

## Tips
- `lemma connectors operations search instagram <query>` — find more operations
- `lemma connectors operations details instagram <OPERATION>` — see full input schema