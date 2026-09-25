# WhatsApp Business

WhatsApp Business enables customer messaging and support automation through the world’s most popular encrypted messaging app. Support teams, sales agents, and chatbots use it to send messages, share media, and manage conversations from a single connector.

**Auth config name:** `whatsapp`

## Common Tasks

### Send a text message
Use when you need to deliver a plain text update, answer, or notification to a customer.
```
lemma connectors operations execute whatsapp send_text_message -d '{"payload": {"to": "15551234567", "body": "Hi Jane, your order #9924 has been dispatched. Track it: https://example.com/track/9924"}}'
```

### Send a media file
Use when you need to share an image, document, or video with a customer.
```
lemma connectors operations execute whatsapp send_media_message -d '{"payload": {"to": "15551234567", "media_url": "https://cdn.example.com/invoices/inv_8011.pdf", "media_type": "document", "caption": "Your invoice #8011"}}'
```

### Send a template message
Use to initiate an outbound conversation with a pre-approved template, such as order confirmations or appointment reminders.
```
lemma connectors operations execute whatsapp send_template_message -d '{"payload": {"to": "15551234567", "template_name": "order_confirmation", "language_code": "en_US", "components": [{"type": "body", "parameters": [{"type": "text", "text": "Jane"}, {"type": "text", "text": "#9924"}, {"type": "text", "text": "https://track.example.com/9924"}]}]}}'
```

### Check message delivery status
Use when you need to confirm whether a message was delivered, read, or failed.
```
lemma connectors operations execute whatsapp get_message_status -d '{"payload": {"message_id": "wamid.HBgMMTU1NTEyMzQ1NjdYABIaABQaGzFBNUFERjNEMUUwNERBMjZBMUFFQS9BQTdGNDQ1QQA="}}'
```

### Retrieve recent conversation
Use when you need to fetch the last messages exchanged with a specific customer, for example to provide context before replying.
```
lemma connectors operations execute whatsapp get_conversation -d '{"payload": {"customer_phone": "15551234567", "limit": 10, "before": "2025-02-18T14:30:00Z"}}'
```

### Mark a message as read
Use after your team reads an incoming message, so the customer sees the blue check marks.
```
lemma connectors operations execute whatsapp mark_message_read -d '{"payload": {"message_id": "wamid.HBgMMTU1NTEyMzQ1NjdYABIaABQaGzFBNUFERjNEMUUwNERBMjZBMUFFQS9BQTdGNDQ1QQA="}}'
```

### Validate a phone number
Use before sending a message to verify the number is registered on WhatsApp.
```
lemma connectors operations execute whatsapp validate_phone_number -d '{"payload": {"phone": "15551234567"}}'
```

## Tips
- `lemma connectors operations search whatsapp <query>` — find more operations
- `lemma connectors operations details whatsapp <OPERATION>` — see full input schema