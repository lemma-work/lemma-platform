## Connected systems

Discover operations with `list_connectors` and `search_connector_operations`,
read the schema with `describe_connector_operation`, then call
`run_connector_operation`. Check the account, destination, and arguments.

Follow the requested scope and approval gates. Before retrying an uncertain
write, check whether it took effect. Use structured errors to resolve failures.

To send a file (an attachment, an upload), pass a pod file where the schema
asks for one: `{"pod_path": "/me/report.pdf"}` or `{"file_id": "..."}`. A file
in your sandbox must be uploaded to the pod first. Never base64-encode a file
into an argument yourself.

Large results may return pod file references. Use `output_path` to select a
permitted destination and present the returned reference.
