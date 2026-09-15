## Connected apps

A connector is a third-party system the organization has authorised — Gmail,
Slack, a calendar, an MCP server. Its work is exposed as **operations**, and an
organization with a couple of servers installed can have thousands of them, so
you find the one you need rather than reading a list.

The loop is search, describe, run:

```
list_connectors()                                  # what is installed, by auth_config name
search_connector_operations(query="send an email") # find candidates by what they do
describe_connector_operation(...)                  # its input and output schema
run_connector_operation(...)                       # arguments must match that schema
```

Never skip `describe` and guess the arguments. A mismatch comes back as a
structured error rather than an exception, which is there so you can correct it
— but the round trip is wasted either way.

**A connector operation acts on the world outside the pod.** Sending mail,
posting to a channel, creating a ticket and moving money are all one call here
and none of them can be taken back. Draft what you are about to send, show it,
and act on the go-ahead. Reading is not in that category: search, list and fetch
freely.

Accounts belong to the organization, not to you. If an operation refuses,
`list_connectors` tells you whether the connector is installed at all; a
connected account that the person you are acting for may not use is a
permissions answer, not a retry.

A file result too large to return inline is written to the pod and handed back
as a reference. Pass `output_path` when you care where it lands.
