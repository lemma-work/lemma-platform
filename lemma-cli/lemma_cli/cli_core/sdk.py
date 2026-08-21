from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .context import selected_pod
from .state import CliState, fail

if TYPE_CHECKING:
    # Type-only import: with `from __future__ import annotations` these names are
    # never evaluated at runtime, so the heavy SDK client is not imported just by
    # loading this module (which the CLI app does at startup for every command).
    from lemma_sdk import Lemma, Pod


class _FlatResourceProxy:
    def __init__(self, resource, pod_id: str):  # type: ignore[no-untyped-def]
        self._resource = resource
        self._pod_id = pod_id

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        aliases = {
            "permissions": "get_permissions",
            "resume": "resume_run",
            "download_to": "download",
            "download_markdown_to": "download_markdown",
        }
        target = aliases.get(name, name)
        if name == "create_for_agent":
            return self._create_for_agent
        if name == "send_stream":
            return self._send_stream
        if name == "update" and not hasattr(self._resource, "update"):
            # Older flat clients expose workflow updates only as update_graph.
            return self._update_graph
        if name == "resume" and target == "resume_run":
            return self._resume_run
        if name == "run":
            return self._run_function
        if name == "replace_permissions":
            return self._replace_permissions
        if name == "upload":
            return self._upload
        if name in {
            "download_to",
            "download_markdown_to",
        }:
            return self._download(name)
        attr = getattr(self._resource, target)
        if not callable(attr):
            return attr

        def call(*args, **kwargs):  # type: ignore[no-untyped-def]
            return attr(self._pod_id, *args, **kwargs)

        return call

    @staticmethod
    def _plain(value):  # type: ignore[no-untyped-def]
        if hasattr(value, "to_dict"):
            return value.to_dict()
        return value

    def _create_for_agent(  # type: ignore[no-untyped-def]
        self,
        agent: str,
        *,
        title: str | None = None,
        parent_id: str | None = None,
    ):
        body = {"agent_name": agent or None, "title": title}
        if parent_id:
            body["parent_id"] = parent_id
        return self._resource.create(self._pod_id, body)

    def _send_stream(self, conversation_id: str, message: str):  # type: ignore[no-untyped-def]
        return self._resource.send_message(
            self._pod_id,
            conversation_id,
            content=message,
            stream=True,
        )

    def _update_graph(self, workflow: str, payload):  # type: ignore[no-untyped-def]
        return self._resource.update_graph(
            self._pod_id,
            workflow,
            **self._plain(payload),
        )

    def _resume_run(self, run: str, inputs=None):  # type: ignore[no-untyped-def]
        return self._resource.resume_run(self._pod_id, run, inputs=inputs)

    def _run_function(self, function: str, input_data=None):  # type: ignore[no-untyped-def]
        return self._resource.run(self._pod_id, function, input_data=input_data)

    def _replace_permissions(self, name: str, payload):  # type: ignore[no-untyped-def]
        return self._resource.replace_permissions(
            self._pod_id,
            name,
            self._plain(payload),
        )

    def _upload(self, local_file, **kwargs):  # type: ignore[no-untyped-def]
        return self._resource.upload(
            self._pod_id,
            file_path=str(local_file),
            **kwargs,
        )

    def _download(self, name: str):  # type: ignore[no-untyped-def]
        target = {
            "download_to": "download",
            "download_markdown_to": "download_markdown",
        }[name]
        method = getattr(self._resource, target)

        def call(remote_path, local_path, **kwargs):  # type: ignore[no-untyped-def]
            return method(
                self._pod_id,
                remote_path,
                **{"output_path": str(Path(local_path)), **kwargs},
            )

        return call


class _FlatPodProxy:
    def __init__(self, client, pod_id: str):  # type: ignore[no-untyped-def]
        self._client = client
        self.pod_id = pod_id

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        return _FlatResourceProxy(getattr(self._client, name), self.pod_id)


def _is_uuid(value: str) -> bool:
    import uuid

    try:
        uuid.UUID(str(value))
        return True
    except ValueError, AttributeError, TypeError:
        return False


def _ensure_pod_uuid(client: Lemma, state: CliState, pod_id: str) -> str:
    """Pod-scoped routes need a UUID. Resolve a name/slug selector to its id.

    Stored defaults are already UUIDs, so the common path skips the lookup; only
    an explicit `--pod <name>` pays for resolution.
    """
    if _is_uuid(pod_id):
        return pod_id
    # Resolution needs a real catalog-capable client (pods + settings). Minimal
    # clients without API access fall through to passthrough unchanged.
    if not hasattr(client, "pods") or not hasattr(client, "settings"):
        return pod_id
    from .context import resolve_pod

    resolved = resolve_pod(client, state, pod_id)
    return str(resolved.get("id") or pod_id)


def pod_client(client: Lemma, state: CliState, pod: str | None = None) -> Pod:
    pod_id = selected_pod(state, pod)
    if not pod_id:
        fail("No pod selected. Run `lemma pods`, pass --pod, or set LEMMA_POD_ID.")
    pod_id = _ensure_pod_uuid(client, state, pod_id)
    if not hasattr(client, "pod"):
        return _FlatPodProxy(client, pod_id)  # type: ignore[return-value]
    return client.pod(pod_id)
