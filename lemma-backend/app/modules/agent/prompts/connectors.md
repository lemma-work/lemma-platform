## Connected systems

Connectors expose operations on external systems. Discover an operation and
read its schema before invoking it:

```
list_connectors()
search_connector_operations(query="send an email")
describe_connector_operation(...)
run_connector_operation(...)
```

Check which account, destination, and resources an operation will use. Execute
within the user's request or applicable standing instruction and respect any
approval gate. If approval is needed, prepare the exact content or change for
review. An account connection does not authorize every operation it exposes.

Read a structured failure before deciding whether to correct the arguments,
resolve account access, or retry. After an uncertain write, check whether it
took effect before repeating it. Report only the outcome the result establishes.

Large file results may be stored in the pod and returned as references. Use
`output_path` when a particular permitted destination is needed, and present
the returned reference.
