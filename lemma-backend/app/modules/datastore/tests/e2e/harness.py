"""Shared E2E harness for the datastore test suite.

Houses the ``DatastoreApi`` HTTP wrapper and the reusable helpers (PDF
building, user signup, pod invitation, indexing) that the per-area datastore
e2e files import. Fixtures live in ``conftest.py``; this module is pure
helpers + the wrapper class so it can be imported freely without pytest
collection side effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import status
from httpx import AsyncClient

# Real arXiv papers (under tests/fixtures/arxiv/) used by the indexing/search
# e2e so conversion + full-text/vector search run against genuine PDF text
# rather than synthetic stand-ins. Each entry pairs a paper with a phrase that
# Kreuzberg reliably extracts from it, so search assertions stay deterministic.
_ARXIV_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "arxiv"


@dataclass(frozen=True)
class Paper:
    filename: str
    # A phrase that reliably appears in the extracted text (used as a search
    # query that should match this paper and no other in the corpus).
    needle: str


PAPERS: dict[str, Paper] = {
    "attention": Paper("attention_is_all_you_need.pdf", "Attention Is All You Need"),
    "seq2seq": Paper("seq2seq.pdf", "NEURAL MACHINE TRANSLATION"),
    "resnet": Paper("resnet.pdf", "Deep Residual Learning"),
    "bert": Paper("bert.pdf", "BERT"),
}


@lru_cache(maxsize=None)
def load_paper(key: str) -> bytes:
    """Read a real arXiv paper fixture by registry key (cached)."""
    return (_ARXIV_DIR / PAPERS[key].filename).read_bytes()


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _mode_params(mode: str | None) -> dict[str, str] | None:
    """Query params carrying the record ``mode`` (e.g. ``admin``), or None."""
    return {"mode": mode} if mode is not None else None


def pod_payload(organization_id: str) -> dict:
    suffix = uuid4().hex[:8]
    return {
        "name": f"Datastore Pod {suffix}",
        "slug": f"datastore-pod-{suffix}",
        "type": "ASSISTANT",
        "organization_id": organization_id,
    }


def escape_pdf_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_pdf_bytes(*lines: str) -> bytes:
    content_lines = ["BT", "/F1 12 Tf", "72 760 Td"]
    for index, line in enumerate(lines):
        escaped = escape_pdf_text(line)
        if index == 0:
            content_lines.append(f"({escaped}) Tj")
        else:
            content_lines.append("0 -18 Td")
            content_lines.append(f"({escaped}) Tj")
    content_lines.append("ET")
    stream = "\n".join(content_lines).encode("latin-1", errors="replace")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(stream)} >>\nstream\n".encode("ascii")
        + stream
        + b"\nendstream",
    ]

    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode("ascii"))
        output.extend(obj)
        output.extend(b"\nendobj\n")

    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(output)


@dataclass
class DatastoreApi:
    client: AsyncClient
    pod_id: str
    user: dict[str, str] | None = None

    def _headers(self) -> dict[str, str] | None:
        if not self.user:
            return None
        return auth_headers(self.user["token"])

    async def request(self, method: str, path: str, **kwargs):
        return await self.client.request(
            method, path, headers=self._headers(), **kwargs
        )

    async def create_folder(
        self,
        path: str,
        *,
        visibility: str | None = None,
        expected_status: int = status.HTTP_201_CREATED,
    ) -> dict:
        payload: dict[str, object] = {"path": path}
        if visibility is not None:
            payload["visibility"] = visibility
        response = await self.request(
            "POST",
            f"/pods/{self.pod_id}/datastore/files/folders",
            json=payload,
        )
        assert response.status_code == expected_status, response.text
        return response.json() if response.content else {}

    async def upload_file(
        self,
        filename: str,
        content: bytes,
        *,
        directory_path: str = "/",
        visibility: str | None = None,
        search_enabled: bool = True,
        content_type: str = "text/markdown",
        expected_status: int = status.HTTP_201_CREATED,
    ) -> dict:
        form_data: dict[str, str] = {
            "directory_path": directory_path,
            "search_enabled": "true" if search_enabled else "false",
        }
        if visibility is not None:
            form_data["visibility"] = visibility
        response = await self.request(
            "POST",
            f"/pods/{self.pod_id}/datastore/files",
            data=form_data,
            files={"data": (filename, content, content_type)},
        )
        assert response.status_code == expected_status, response.text
        return response.json() if response.content else {}

    async def list_files(
        self,
        *,
        directory_path: str = "/",
        limit: int = 100,
        page_token: str | None = None,
        expected_status: int = status.HTTP_200_OK,
    ) -> dict:
        params = {
            "directory_path": directory_path,
            "limit": limit,
        }
        if page_token:
            params["page_token"] = page_token
        response = await self.request(
            "GET",
            f"/pods/{self.pod_id}/datastore/files",
            params=params,
        )
        assert response.status_code == expected_status, response.text
        return response.json()

    async def get_file(
        self,
        path: str,
        *,
        expected_status: int = status.HTTP_200_OK,
    ) -> dict:
        response = await self.request(
            "GET",
            f"/pods/{self.pod_id}/datastore/files/by-path",
            params={"path": path},
        )
        assert response.status_code == expected_status, response.text
        return response.json() if response.content else {}

    async def update_file(
        self,
        path: str,
        *,
        new_path: str | None = None,
        content: bytes | None = None,
        filename: str = "updated.md",
        search_enabled: bool | None = None,
        expected_status: int = status.HTTP_200_OK,
    ) -> dict:
        data = {"path": path}
        if new_path is not None:
            data["new_path"] = new_path
        if search_enabled is not None:
            data["search_enabled"] = "true" if search_enabled else "false"
        files = None
        if content is not None:
            files = {"data": (filename, content, "text/markdown")}
        response = await self.request(
            "PATCH",
            f"/pods/{self.pod_id}/datastore/files/by-path",
            data=data,
            files=files,
        )
        assert response.status_code == expected_status, response.text
        return response.json() if response.content else {}

    async def download_file(
        self,
        path: str,
        *,
        expected_status: int = status.HTTP_200_OK,
    ) -> bytes:
        response = await self.request(
            "GET",
            f"/pods/{self.pod_id}/datastore/files/download",
            params={"path": path},
        )
        assert response.status_code == expected_status, response.text
        return response.content

    async def delete_file(
        self,
        path: str,
        *,
        expected_status: int = status.HTTP_204_NO_CONTENT,
    ) -> dict:
        response = await self.request(
            "DELETE",
            f"/pods/{self.pod_id}/datastore/files/by-path",
            params={"path": path},
        )
        assert response.status_code == expected_status, response.text
        return response.json() if response.content else {}

    async def tree(
        self,
        *,
        root_path: str = "/",
        files_per_directory: int = 3,
    ) -> dict:
        response = await self.request(
            "GET",
            f"/pods/{self.pod_id}/datastore/files/tree",
            params={
                "root_path": root_path,
                "files_per_directory": files_per_directory,
            },
        )
        assert response.status_code == status.HTTP_200_OK, response.text
        return response.json()

    async def search_files(
        self,
        query: str,
        *,
        search_method: str = "TEXT",
        scope_path: str | None = None,
        scope_mode: str = "SUBTREE",
        limit: int = 10,
        expected_status: int = status.HTTP_200_OK,
    ) -> dict:
        payload: dict[str, object] = {
            "query": query,
            "search_method": search_method,
            "scope_mode": scope_mode,
            "limit": limit,
        }
        if scope_path:
            payload["scope_path"] = scope_path
        response = await self.request(
            "POST",
            f"/pods/{self.pod_id}/datastore/files/search",
            json=payload,
        )
        assert response.status_code == expected_status, response.text
        return response.json() if response.content else {}

    async def list_children(
        self,
        path: str,
        *,
        expected_status: int = status.HTTP_200_OK,
    ) -> dict:
        """List a document's derived child files (converted markdown, figures,
        renderable pages)."""
        response = await self.request(
            "GET",
            f"/pods/{self.pod_id}/datastore/files/children",
            params={"path": path},
        )
        assert response.status_code == expected_status, response.text
        return response.json() if response.content else {}

    async def child_content(
        self,
        path: str,
        *,
        page_start: int | None = None,
        page_end: int | None = None,
        expected_status: int = status.HTTP_200_OK,
    ) -> bytes:
        """Fetch a single child artifact by its ``/<file>/<artifact>`` path."""
        params: dict[str, object] = {"path": path}
        if page_start is not None:
            params["page_start"] = page_start
        if page_end is not None:
            params["page_end"] = page_end
        response = await self.request(
            "GET",
            f"/pods/{self.pod_id}/datastore/files/children/content",
            params=params,
        )
        assert response.status_code == expected_status, response.text
        return response.content

    async def file_url(
        self,
        path: str,
        *,
        expected_status: int = status.HTTP_200_OK,
    ) -> dict:
        response = await self.request(
            "GET",
            f"/pods/{self.pod_id}/datastore/files/url",
            params={"path": path},
        )
        assert response.status_code == expected_status, response.text
        return response.json() if response.content else {}

    async def create_table(
        self, payload: dict, expected_status: int = status.HTTP_201_CREATED
    ):
        response = await self.request(
            "POST",
            f"/pods/{self.pod_id}/datastore/tables",
            json=payload,
        )
        assert response.status_code == expected_status, response.text
        return response.json() if response.content else {}

    async def list_tables(self, **params) -> dict:
        response = await self.request(
            "GET",
            f"/pods/{self.pod_id}/datastore/tables",
            params=params,
        )
        assert response.status_code == status.HTTP_200_OK, response.text
        return response.json()

    async def get_table(
        self, table_name: str, expected_status: int = status.HTTP_200_OK
    ) -> dict:
        response = await self.request(
            "GET",
            f"/pods/{self.pod_id}/datastore/tables/{table_name}",
        )
        assert response.status_code == expected_status, response.text
        return response.json() if response.content else {}

    async def update_table(
        self,
        table_name: str,
        payload: dict,
        *,
        expected_status: int = status.HTTP_200_OK,
    ) -> dict:
        response = await self.request(
            "PATCH",
            f"/pods/{self.pod_id}/datastore/tables/{table_name}",
            json=payload,
        )
        assert response.status_code == expected_status, response.text
        return response.json() if response.content else {}

    async def add_column(self, table_name: str, column: dict) -> dict:
        response = await self.request(
            "POST",
            f"/pods/{self.pod_id}/datastore/tables/{table_name}/columns",
            json={"column": column},
        )
        assert response.status_code == status.HTTP_200_OK, response.text
        return response.json()

    async def remove_column(
        self,
        table_name: str,
        column_name: str,
        *,
        expected_status: int = status.HTTP_204_NO_CONTENT,
    ) -> dict:
        response = await self.request(
            "DELETE",
            f"/pods/{self.pod_id}/datastore/tables/{table_name}/columns/{column_name}",
        )
        assert response.status_code == expected_status, response.text
        return response.json() if response.content else {}

    async def delete_table(
        self,
        table_name: str,
        *,
        expected_status: int = status.HTTP_204_NO_CONTENT,
    ) -> dict:
        response = await self.request(
            "DELETE",
            f"/pods/{self.pod_id}/datastore/tables/{table_name}",
        )
        assert response.status_code == expected_status, response.text
        return response.json() if response.content else {}

    async def create_record(
        self,
        table_name: str,
        data: dict,
        *,
        expected_status: int = status.HTTP_201_CREATED,
    ) -> dict:
        response = await self.request(
            "POST",
            f"/pods/{self.pod_id}/datastore/tables/{table_name}/records",
            json={"data": data},
        )
        assert response.status_code == expected_status, response.text
        return (
            response.json()
            if response.content and response.status_code < 400
            else {}
        )

    async def list_records(self, table_name: str, **params) -> dict:
        response = await self.request(
            "GET",
            f"/pods/{self.pod_id}/datastore/tables/{table_name}/records",
            params=params,
        )
        assert response.status_code == status.HTTP_200_OK, response.text
        return response.json()

    async def get_record(
        self,
        table_name: str,
        record_id,
        *,
        mode: str | None = None,
        expected_status: int = status.HTTP_200_OK,
    ) -> dict:
        response = await self.request(
            "GET",
            f"/pods/{self.pod_id}/datastore/tables/{table_name}/records/{record_id}",
            params=_mode_params(mode),
        )
        assert response.status_code == expected_status, response.text
        return (
            response.json()
            if response.content and response.status_code < 400
            else {}
        )

    async def update_record(
        self,
        table_name: str,
        record_id,
        data: dict,
        *,
        mode: str | None = None,
        expected_status: int = status.HTTP_200_OK,
    ) -> dict:
        response = await self.request(
            "PATCH",
            f"/pods/{self.pod_id}/datastore/tables/{table_name}/records/{record_id}",
            params=_mode_params(mode),
            json={"data": data},
        )
        assert response.status_code == expected_status, response.text
        return response.json() if response.content and response.status_code < 400 else {}

    async def delete_record(
        self,
        table_name: str,
        record_id,
        *,
        mode: str | None = None,
        expected_status: int = status.HTTP_204_NO_CONTENT,
    ) -> dict:
        response = await self.request(
            "DELETE",
            f"/pods/{self.pod_id}/datastore/tables/{table_name}/records/{record_id}",
            params=_mode_params(mode),
        )
        assert response.status_code == expected_status, response.text
        return response.json() if response.content else {}

    async def bulk_create(
        self, table_name: str, records: list[dict], *, upsert: bool = False
    ):
        response = await self.request(
            "POST",
            f"/pods/{self.pod_id}/datastore/tables/{table_name}/records/bulk/create",
            json={"records": records, "upsert": upsert},
        )
        assert response.status_code == status.HTTP_200_OK, response.text
        return response.json()

    async def bulk_update(
        self,
        table_name: str,
        records: list[dict],
        *,
        mode: str | None = None,
        expected_status: int = status.HTTP_200_OK,
    ):
        response = await self.request(
            "POST",
            f"/pods/{self.pod_id}/datastore/tables/{table_name}/records/bulk/update",
            params=_mode_params(mode),
            json={"records": records},
        )
        assert response.status_code == expected_status, response.text
        return response.json() if response.content else {}

    async def bulk_delete(
        self,
        table_name: str,
        record_ids: list,
        *,
        mode: str | None = None,
        expected_status: int = status.HTTP_200_OK,
    ):
        response = await self.request(
            "POST",
            f"/pods/{self.pod_id}/datastore/tables/{table_name}/records/bulk/delete",
            params=_mode_params(mode),
            json={"record_ids": record_ids},
        )
        assert response.status_code == expected_status, response.text
        return response.json() if response.content else {}

    async def query(
        self,
        sql: str,
        *,
        mode: str | None = None,
        expected_status: int = status.HTTP_200_OK,
    ) -> dict:
        response = await self.request(
            "POST",
            f"/pods/{self.pod_id}/datastore/query",
            params=_mode_params(mode),
            json={"query": sql},
        )
        assert response.status_code == expected_status, response.text
        return response.json() if response.content else {}


async def signup_user(async_client: AsyncClient, prefix: str) -> dict[str, str]:
    email = f"test+{prefix}-{uuid4().hex[:8]}@example.com"
    response = await async_client.post(
        "/st/auth/signup",
        json={
            "formFields": [
                {"id": "email", "value": email},
                {"id": "password", "value": "TestPassword@123"},
            ]
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    token = response.headers.get("st-access-token") or response.cookies.get(
        "sAccessToken"
    )
    assert payload.get("status") == "OK", payload
    assert token
    return {"email": email, "token": token, "id": payload["user"]["id"]}


async def invite_to_pod(
    owner_client: AsyncClient,
    async_client: AsyncClient,
    *,
    org_id: str,
    pod_id: str,
    user: dict[str, str],
    role: str,
) -> None:
    invite = await owner_client.post(
        f"/organizations/{org_id}/invitations",
        json={"email": user["email"], "role": "ORG_MEMBER"},
    )
    assert invite.status_code == status.HTTP_201_CREATED, invite.text
    accept = await async_client.post(
        f"/organizations/invitations/{invite.json()['id']}/accept",
        headers=auth_headers(user["token"]),
    )
    assert accept.status_code == status.HTTP_200_OK, accept.text
    members = await owner_client.get(f"/organizations/{org_id}/members")
    assert members.status_code == status.HTTP_200_OK, members.text
    member = next(
        item
        for item in members.json()["items"]
        if item.get("user", {}).get("email") == user["email"]
    )
    added = await owner_client.post(
        f"/pods/{pod_id}/members",
        json={"organization_member_id": member["id"], "roles": [role]},
    )
    assert added.status_code == status.HTTP_201_CREATED, added.text


async def index_file(index_datastore_file, file_entity: dict) -> None:
    await index_datastore_file(UUID(file_entity["pod_id"]), UUID(file_entity["id"]))
