"""File arguments for connector operations.

A connector operation that takes a file -- a Gmail attachment, a Drive upload
-- takes a *reference* to one, and the server reads it with the caller's own
access::

    pod.connectors.execute("gmail", "GMAIL_SEND_EMAIL", {
        "recipient_email": "ada@example.com",
        "subject": "Q3",
        "body": "Attached.",
        "attachment": pod_file("/me/reports/q3.pdf"),
    })

These only build the dict; writing it by hand is equally valid.
"""

from __future__ import annotations

from uuid import UUID


def pod_file(path: str, *, filename: str | None = None) -> dict[str, str]:
    """A file in the pod, by path -- ``/me/...`` is the caller's own folder."""
    return {"pod_path": path, **({"filename": filename} if filename else {})}


def pod_file_by_id(
    file_id: str | UUID, *, filename: str | None = None
) -> dict[str, str]:
    """A file in the pod, by id: stable across renames, and the same file for
    whoever runs the call, where ``/me`` is not."""
    return {"file_id": str(file_id), **({"filename": filename} if filename else {})}


__all__ = ["pod_file", "pod_file_by_id"]
