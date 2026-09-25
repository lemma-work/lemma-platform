# Facebook
Facebook is a social media and advertising platform used by businesses and creators to connect with audiences, publish content, and manage Page interactions. It supports Page-level actions like posting, commenting, and retrieving engagement.

**Auth config name:** `facebook`

## Common Tasks

### Publish a text or link post
Use this to immediately share an update, article link, or announcement on your Page.
```
lemma connectors operations execute facebook FACEBOOK_CREATE_POST -d '{"payload": {"message": "We're launching our spring collection this Friday! Stay tuned.", "link": "https://www.example.com/spring-preview", "page_id": "103456789012345", "published": true}}'
```

### Schedule a post for later
Schedule a post to go live at a specific future time, ideal for planning content ahead.
```
lemma connectors operations execute facebook FACEBOOK_CREATE_POST -d '{"payload": {"message": "Our summer sale starts next week! Mark your calendars.", "link": "https://www.example.com/summer-sale", "page_id": "103456789012345", "published": false, "scheduled_publish_time": 1746076800}}'
```

### Share a photo post with a caption
Use this when you have a public image URL and want to publish a photo with an engaging caption.
```
lemma connectors operations execute facebook FACEBOOK_CREATE_PHOTO_POST -d '{"payload": {"url": "https://images.unsplash.com/photo-1506744038136-46273834b3fb", "message": "New mural in the office.", "page_id": "103456789012345", "published": true}}'
```

### Upload a video from a URL
Post a video hosted externally (e.g., a product demo) directly to your Page’s timeline.
```
lemma connectors operations execute facebook FACEBOOK_CREATE_VIDEO_POST -d '{"payload": {"file_url": "https://www.w3schools.com/html/mov_bbb.mp4", "description": "Quick tour of our new workspace.", "page_id": "103456789012345", "published": true}}'
```

### Comment on a post
Reply to a user’s comment or add a comment to a Page post to engage with your audience.
```
lemma connectors operations execute facebook FACEBOOK_CREATE_COMMENT -d '{"payload": {"message": "Thanks for the feedback! We'll look into that.", "object_id": "103456789012345_987654321098765"}}'
```

### Retrieve comments from a post
Fetch recent comments on a post to monitor engagement or collect feedback.
```
lemma connectors operations execute facebook FACEBOOK_GET_COMMENTS -d '{"payload": {"object_id": "103456789012345_987654321098765", "limit": 10, "order": "reverse_chronological", "fields": "id,message,created_time,from"}}'
```

### Fetch Page details
Look up your Page’s name, category, about text, and public contact info.
```
lemma connectors operations execute facebook FACEBOOK_GET_PAGE_DETAILS -d '{"payload": {"page_id": "103456789012345", "fields": "name,category,about,emails,website"}}'
```

## Tips
- `lemma connectors operations search facebook <query>` — find more operations
- `lemma connectors operations details facebook <OPERATION>` — see full input schema