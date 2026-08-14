# lemma-connectors

OpenAPI-native native connector packages for Lemma.

This workspace contains typed connector packages generated from provider OpenAPI
specs and organized around resource clients. Each package exposes:

- a runtime client for executing typed operations and tools
- an info client for discovery, descriptions, and schemas
- generated typed transport/client code
- generated canonical Pydantic schema models from the provider spec
- generated typed tool input/output models per endpoint
- generated resource-based operation modules

## Implemented packages

Current package coverage:

- Gmail: 79 tools / 79 operations
- Google Calendar: 37 / 37
- Google Drive: 48 / 48
- Google Docs: 3 / 3
- Google Sheets: 17 / 17
- Slack: 118 / 118
- Jira: 487 / 487

Slack admin endpoints are intentionally excluded from the generated package surface.

## Package shape

```text
lemma-connectors/
  openapi_specs/
  scripts/
    generate_openapi_metadata.py
    generate_tool_types.py
    generate_resource_operations.py
  src/lemma_connectors/
    core/
    gmail/
      client.py
      generated/
      resources/
    google_calendar/
      client.py
      generated/
      resources/
    google_drive/
      client.py
      generated/
      resources/
    google_docs/
      client.py
      generated/
      resources/
    google_sheets/
      client.py
      generated/
      resources/
    jira/
      client.py
      generated/
      resources/
    slack/
      client.py
      generated/
      resources/
```

## Runtime model

### Typed auth

The shared auth layer supports:

- `OAuth2Credentials`
- `ApiKeyCredentials`
- `NoAuthCredentials`

### Tools

Each OpenAPI endpoint becomes a generated tool with:

- a stable snake_case name
- generated typed request/response execution via the generated client
- generated `tool_types.py` Pydantic models for tool input and output
- metadata for method, path, tags, deprecation, and schemas
- binary/file responses normalized into a typed `BinaryContentResult`
- provider wire-format string fields preserved when eager decoding would be misleading, such as Gmail base64url message bodies

### Operations

Each generated tool also gets a resource-scoped operation surface.

Examples:

- Gmail: `messages_list`, `drafts_send`, `labels_get`
- Google Calendar: `events_insert`, `calendar_list_list`, `freebusy_query`
- Google Drive: `files_list`, `permissions_create`, `revisions_update`
- Jira: `add_comment`, `get_attachment_content`, `search_issues`
- Slack: `chat_post_message`, `conversations_history`, `users_info`

Operation names come from cleaned provider `operationId` values, not raw path ancestry.
Operations are grouped by resource class and exposed through `client.resources`.

## Client usage

```python
from lemma_connectors import GmailClient, OAuth2Credentials

client = GmailClient(
    credentials=OAuth2Credentials(access_token="...")
)

operations = client.list_operations()
tools = client.list_tools()

result = await client.execute_operation(
    "messages_list",
    {"user_id": "me", "max_results": 25},
)

raw_result = await client.execute_tool(
    "gmail_users_messages_list",
    {"user_id": "me", "max_results": 25},
)
```

Resource access is also available:

```python
resource = client.resources.messages
operation = resource.build_operations()["messages_get"]
```

## Generator flow

The package generation flow is:

1. Read the provider OpenAPI spec from `openapi_specs/`.
2. Sanitize content types and app-specific exclusions.
3. Generate a typed OpenAPI client into `generated/client/`.
4. Generate canonical Pydantic schema models into `generated/pydantic_models.py`.
5. Emit tool metadata into `generated/openapi_metadata.json`.
6. Generate typed tool models into `generated/tool_types.py`.
7. Generate resource-scoped operation modules into `resources/`.
8. Validate imports, discovery, and focused tests.

For future provider work, there is a repo-local reusable skill at
[skills/integration-creator/SKILL.md](./skills/integration-creator/SKILL.md).
That skill is meant to be read on demand together with
[skills/integration-creator/references/workflow.md](./skills/integration-creator/references/workflow.md)
when generating a new connector package from an OpenAPI spec.
