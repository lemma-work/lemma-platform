# LinkedIn

LinkedIn is the world’s largest professional network, used by individuals, recruiters, marketers, and organizations to connect, share content, and manage brand presence.

**Auth config name:** `linkedin`

## Common Tasks

### Share an article or URL
Use when you want to publish a link with optional commentary to your LinkedIn feed.
```
lemma connectors operations execute linkedin LINKEDIN_CREATE_ARTICLE_OR_URL_SHARE -d '{"payload": {"author": "urn:li:person:123456789", "visibility": "PUBLIC", "lifecycleState": "PUBLISHED", "specificContent": {"shareCommentary": {"text": "Check out our latest product announcement"}, "shareMediaCategory": "ARTICLE", "content": [{"entityLocation": "https://www.example.com/our-product"}]}}}'
```

### Create a text post
Use when sharing thoughts, updates, or plain-text content with your network or company page.
```
lemma connectors operations execute linkedin LINKEDIN_CREATE_LINKED_IN_POST -d '{"payload": {"author": "urn:li:person:987654321", "commentary": "Hello LinkedIn! Just wrapped up an amazing project.", "visibility": "PUBLIC", "lifecycleState": "PUBLISHED"}}'
```

### Comment on a post
Use to engage with a specific LinkedIn share or UGC post by adding a first-level comment.
```
lemma connectors operations execute linkedin LINKEDIN_CREATE_COMMENT_ON_POST -d '{"payload": {"actor": "urn:li:person:111111111", "object": "urn:li:share:1234567890", "message": {"text": "Great insights, thanks for sharing."}, "target_urn": "urn:li:share:1234567890"}}'
```

### Retrieve your own profile
Use to get the authenticated user’s profile information (name, headline, picture).
```
lemma connectors operations execute linkedin LINKEDIN_GET_MY_INFO -d '{"payload": {}}'
```

### List companies you manage
Use to discover which organization pages you have administrative rights to, assisting with content posting or analytics.
```
lemma connectors operations execute linkedin LINKEDIN_GET_COMPANY_INFO -d '{"payload": {"role": "ADMINISTRATOR", "state": "APPROVED", "count": 10, "start": 0}}'
```

### Get a company’s follower count
Use to check the total number of members following a specific LinkedIn organization page.
```
lemma connectors operations execute linkedin LINKEDIN_GET_NETWORK_SIZE -d '{"payload": {"edgeType": "COMPANY_FOLLOWED_BY_MEMBER", "organization_id": "123456"}}'
```

### Delete a post
Use to remove a previously published LinkedIn share or UGC post using its URN.
```
lemma connectors operations execute linkedin LINKEDIN_DELETE_POST -d '{"payload": {"post_urn": "urn:li:share:5555555555"}}'
```

## Tips
- `lemma connectors operations search linkedin <query>` — find more operations
- `lemma connectors operations details linkedin <OPERATION>` — see full input schema