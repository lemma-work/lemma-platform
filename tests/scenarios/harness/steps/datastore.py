"""What a person does with tables, records, and files in a pod."""

from __future__ import annotations

import json

from typing import Any

from harness.run import a_name_for
from harness.drivers.api import items_of

JSON = dict[str, Any]


def column(
    name: str,
    kind: str = "TEXT",
    *,
    required: bool = False,
    unique: bool = False,
    options: list[str] | None = None,
) -> JSON:
    """One column, in the shape the API wants.

    A small helper rather than a raw dict at every call site: a table with four
    columns is otherwise twenty lines of punctuation in the middle of a
    scenario, and the scenario stops reading as a sentence.
    """
    spec: JSON = {"name": name, "type": kind, "required": required, "unique": unique}
    if options is not None:
        spec["options"] = options
    return spec


class DatastoreSteps:
    """Mixed into :class:`harness.world.Person`."""

    # --- tables ----------------------------------------------------------

    async def creates_a_table(
        self,
        *,
        in_pod: JSON,
        named: str | None = None,
        columns: list[JSON] | None = None,
        visibility: str | None = None,
        shared: bool = False,
    ) -> JSON:
        """Create a table.

        ``shared`` turns row-level security off. It is off-by-default here for
        the same reason it is on-by-default in the API: a table's rows belong to
        the person who wrote them unless someone says otherwise. Pass
        ``shared=True`` for a table a whole team reads and writes — a support
        inbox, a shared CRM — where every member sees every row.
        """
        name = named or a_name_for("table")
        body: JSON = {
            "name": name,
            "columns": columns or [column("title"), column("note")],
        }
        if shared:
            body["enable_rls"] = False
        if visibility is not None:
            body["visibility"] = visibility
        return await self.api.post(
            f"/pods/{in_pod['id']}/datastore/tables",
            what=f"{self.label} creating table {name!r}",
            json=body,
        )

    async def is_refused_creating_a_table(
        self, *, in_pod: JSON, named: str | None = None, columns: list[JSON] | None = None
    ) -> int:
        response = await self.api.call(
            "POST",
            f"/pods/{in_pod['id']}/datastore/tables",
            json={
                "name": named or a_name_for("table"),
                "columns": columns or [column("title")],
            },
        )
        if response.status_code < 400:
            raise AssertionError(
                f"{self.label} was expected to be refused creating a table in "
                f"{in_pod.get('name')!r}, but it succeeded ({response.status_code})"
            )
        return response.status_code

    async def opens_table(self, name: str, *, in_pod: JSON) -> JSON:
        return await self.api.get(
            f"/pods/{in_pod['id']}/datastore/tables/{name}",
            what=f"{self.label} opening table {name!r}",
        )

    async def tables_in(self, pod: JSON) -> list[JSON]:
        return items_of(await self.api.get(f"/pods/{pod['id']}/datastore/tables"))

    async def adds_column(self, spec: JSON, *, to_table: str, in_pod: JSON) -> JSON:
        return await self.api.post(
            f"/pods/{in_pod['id']}/datastore/tables/{to_table}/columns",
            what=f"{self.label} adding column {spec['name']!r} to {to_table!r}",
            json={"column": spec},
        )

    async def is_refused_adding_column(
        self, spec: JSON, *, to_table: str, in_pod: JSON
    ) -> int:
        response = await self.api.call(
            "POST",
            f"/pods/{in_pod['id']}/datastore/tables/{to_table}/columns",
            json={"column": spec},
        )
        if response.status_code < 400:
            raise AssertionError(
                f"{self.label} was expected to be refused adding column "
                f"{spec['name']!r}, but it succeeded ({response.status_code})"
            )
        return response.status_code

    async def removes_column(self, name: str, *, from_table: str, in_pod: JSON) -> None:
        await self.api.delete(
            f"/pods/{in_pod['id']}/datastore/tables/{from_table}/columns/{name}",
            what=f"{self.label} removing column {name!r} from {from_table!r}",
        )

    async def deletes_table(self, name: str, *, in_pod: JSON) -> None:
        await self.api.delete(
            f"/pods/{in_pod['id']}/datastore/tables/{name}",
            what=f"{self.label} deleting table {name!r}",
        )

    async def cannot_find_table(self, name: str, *, in_pod: JSON) -> int:
        response = await self.api.call(
            "GET", f"/pods/{in_pod['id']}/datastore/tables/{name}"
        )
        if response.status_code < 400:
            raise AssertionError(
                f"table {name!r} still exists for {self.label} "
                f"({response.status_code}), but should not"
            )
        return response.status_code

    # --- records ---------------------------------------------------------

    async def adds_record(self, data: JSON, *, to_table: str, in_pod: JSON) -> JSON:
        return await self.api.post(
            f"/pods/{in_pod['id']}/datastore/tables/{to_table}/records",
            what=f"{self.label} adding a record to {to_table!r}",
            json={"data": data},
        )

    async def is_refused_adding_record(
        self, data: JSON, *, to_table: str, in_pod: JSON
    ) -> int:
        response = await self.api.call(
            "POST",
            f"/pods/{in_pod['id']}/datastore/tables/{to_table}/records",
            json={"data": data},
        )
        if response.status_code < 400:
            raise AssertionError(
                f"{self.label} was expected to be refused adding {data} to "
                f"{to_table!r}, but it was accepted ({response.status_code})"
            )
        return response.status_code

    async def adds_records(
        self, rows: list[JSON], *, to_table: str, in_pod: JSON
    ) -> JSON:
        return await self.api.post(
            f"/pods/{in_pod['id']}/datastore/tables/{to_table}/records/bulk/create",
            what=f"{self.label} adding {len(rows)} records to {to_table!r}",
            json={"records": rows},
        )

    async def is_refused_adding_records(
        self, rows: list[JSON], *, to_table: str, in_pod: JSON
    ) -> int:
        response = await self.api.call(
            "POST",
            f"/pods/{in_pod['id']}/datastore/tables/{to_table}/records/bulk/create",
            json={"records": rows},
        )
        if response.status_code < 400:
            raise AssertionError(
                f"{self.label} was expected to be refused a bulk write to "
                f"{to_table!r}, but it was accepted ({response.status_code})"
            )
        return response.status_code

    @staticmethod
    def sorted_by(field: str, direction: str = "asc") -> str:
        """One sort clause, in the shape the API wants.

        Sorts are JSON objects rather than the `field:direction` shorthand most
        APIs take — easy to get wrong, and the error ("Invalid sort parameter")
        does not say what the right shape is. Encoded once, here.
        """
        return json.dumps({"field": field, "direction": direction})

    async def records_in(
        self, table: str, *, in_pod: JSON, everyones: bool = False, **query: Any
    ) -> list[JSON]:
        """Records this person can see in a table.

        ``everyones=True`` asks for every member's rows rather than only this
        person's. It applies to owner-scoped tables and needs permission to
        administer the table; without it an admin sees only what they wrote,
        which is the right default for an app but the wrong one for auditing.
        """
        if everyones:
            query["mode"] = "ADMIN"
        return items_of(
            await self.api.get(
                f"/pods/{in_pod['id']}/datastore/tables/{table}/records",
                params=query or None,
            )
        )

    async def is_refused_everyones_records(self, table: str, *, in_pod: JSON) -> int:
        response = await self.api.call(
            "GET",
            f"/pods/{in_pod['id']}/datastore/tables/{table}/records",
            params={"mode": "ADMIN"},
        )
        if response.status_code < 400:
            raise AssertionError(
                f"{self.label} was expected to be refused every member's rows in "
                f"{table!r}, but got them ({response.status_code})"
            )
        return response.status_code

    async def updates_record(
        self, record: JSON, *, data: JSON, in_table: str, in_pod: JSON
    ) -> JSON:
        return await self.api.patch(
            f"/pods/{in_pod['id']}/datastore/tables/{in_table}/records/{record['id']}",
            what=f"{self.label} updating a record in {in_table!r}",
            json={"data": data},
        )

    async def deletes_record(self, record: JSON, *, in_table: str, in_pod: JSON) -> None:
        await self.api.delete(
            f"/pods/{in_pod['id']}/datastore/tables/{in_table}/records/{record['id']}",
            what=f"{self.label} deleting a record from {in_table!r}",
        )

    # --- files -----------------------------------------------------------

    async def uploads(
        self,
        *,
        content: bytes,
        named: str,
        in_pod: JSON,
        directory: str = "/",
        content_type: str = "text/plain",
        searchable: bool = False,
    ) -> JSON:
        """Upload a file. ``searchable`` off by default.

        Indexing is background work needing the document-extraction service, and
        most scenarios are about the file being *there*, not about it being
        searchable. The ones that care turn it on and wait for it.
        """
        return await self.api.post(
            f"/pods/{in_pod['id']}/datastore/files",
            what=f"{self.label} uploading {named!r} to {directory!r}",
            files={"data": (named, content, content_type)},
            data={
                "directory_path": directory,
                "name": named,
                "search_enabled": str(searchable).lower(),
            },
        )

    async def opens_file(self, path: str, *, in_pod: JSON) -> JSON:
        return await self.api.get(
            f"/pods/{in_pod['id']}/datastore/files/by-path",
            params={"path": path},
            what=f"{self.label} opening {path!r}",
        )

    async def files_in(self, pod: JSON) -> list[JSON]:
        return items_of(await self.api.get(f"/pods/{pod['id']}/datastore/files"))

    async def deletes_file(self, path: str, *, in_pod: JSON) -> None:
        await self.api.delete(
            f"/pods/{in_pod['id']}/datastore/files/by-path",
            params={"path": path},
            what=f"{self.label} deleting {path!r}",
        )

    async def is_refused_file(self, path: str, *, in_pod: JSON) -> int:
        response = await self.api.call(
            "GET",
            f"/pods/{in_pod['id']}/datastore/files/by-path",
            params={"path": path},
        )
        if response.status_code < 400:
            raise AssertionError(
                f"{self.label} was expected to be refused {path!r}, but read it "
                f"({response.status_code})"
            )
        return response.status_code

    async def creates_a_folder(self, *, at_path: str, in_pod: JSON) -> JSON:
        return await self.api.post(
            f"/pods/{in_pod['id']}/datastore/files/folders",
            what=f"{self.label} creating folder {at_path!r}",
            json={"path": at_path},
        )

    async def file_tree_of(self, pod: JSON) -> Any:
        return await self.api.get(f"/pods/{pod['id']}/datastore/files/tree")

    async def searches_files(self, query: str, *, in_pod: JSON, limit: int = 10) -> Any:
        return await self.api.post(
            f"/pods/{in_pod['id']}/datastore/files/search",
            what=f"{self.label} searching files for {query!r}",
            json={"query": query, "limit": limit},
        )

    async def signed_link_to(self, path: str, *, in_pod: JSON) -> JSON:
        return await self.api.post(
            f"/pods/{in_pod['id']}/datastore/files/signed-url",
            params={"path": path},
            what=f"{self.label} making a signed link to {path!r}",
        )

    async def downloads(self, path: str, *, in_pod: JSON) -> bytes:
        response = await self.api.call(
            "GET",
            f"/pods/{in_pod['id']}/datastore/files/download",
            params={"path": path},
        )
        if response.status_code != 200:
            raise AssertionError(
                f"{self.label} could not download {path!r}: {response.status_code}\n"
                f"  body: {response.text[:500]}"
            )
        return response.content

    async def changes_table(self, name: str, *, in_pod: JSON, **changes: Any) -> JSON:
        return await self.api.patch(
            f"/pods/{in_pod['id']}/datastore/tables/{name}",
            what=f"{self.label} updating table {name!r}",
            json=changes,
        )

    async def updates_records(
        self, rows: list[JSON], *, in_table: str, in_pod: JSON
    ) -> JSON:
        return await self.api.post(
            f"/pods/{in_pod['id']}/datastore/tables/{in_table}/records/bulk/update",
            what=f"{self.label} updating {len(rows)} records in {in_table!r}",
            json={"records": rows},
        )

    async def deletes_records(
        self, record_ids: list[str], *, in_table: str, in_pod: JSON
    ) -> JSON:
        return await self.api.post(
            f"/pods/{in_pod['id']}/datastore/tables/{in_table}/records/bulk/delete",
            what=f"{self.label} deleting {len(record_ids)} records from {in_table!r}",
            json={"record_ids": [str(r) for r in record_ids]},
        )

    async def asks(self, query: str, *, in_pod: JSON, everyones: bool = False) -> JSON:
        """Run a read-only query against the pod's own data."""
        return await self.api.post(
            f"/pods/{in_pod['id']}/datastore/query",
            what=f"{self.label} querying {in_pod.get('name')!r}",
            params={"mode": "ADMIN"} if everyones else None,
            json={"query": query},
        )

    async def is_refused_query(self, query: str, *, in_pod: JSON) -> int:
        response = await self.api.call(
            "POST", f"/pods/{in_pod['id']}/datastore/query", json={"query": query}
        )
        if response.status_code < 400:
            raise AssertionError(
                f"{self.label} was expected to be refused the query {query!r}, "
                f"but it ran ({response.status_code})"
            )
        return response.status_code

    async def moves_file(self, path: str, *, to: str, in_pod: JSON) -> JSON:
        # Multipart, not JSON: the same endpoint can replace the bytes, so it
        # takes a form whether or not this call carries any.
        # Every field goes through `files` with a `None` filename: that is how
        # httpx is told to send multipart at all. Passing `data=` alone sends
        # form-urlencoded, which this endpoint rejects.
        return await self.api.patch(
            f"/pods/{in_pod['id']}/datastore/files/by-path",
            what=f"{self.label} moving {path!r} to {to!r}",
            files={"path": (None, path), "new_path": (None, to)},
        )

    async def link_to(self, path: str, *, in_pod: JSON) -> JSON:
        return await self.api.get(
            f"/pods/{in_pod['id']}/datastore/files/url", params={"path": path}
        )

    async def opens_file_by_id(self, file_id: str, *, in_pod: JSON) -> JSON:
        return await self.api.get(f"/pods/{in_pod['id']}/datastore/files/{file_id}")

    async def children_of(self, path: str, *, in_pod: JSON) -> list[JSON]:
        return items_of(
            await self.api.get(
                f"/pods/{in_pod['id']}/datastore/files/children", params={"path": path}
            )
        )

    async def attaches_markdown(self, text: str, *, to_path: str, in_pod: JSON) -> JSON:
        return await self.api.put(
            f"/pods/{in_pod['id']}/datastore/files/by-path/markdown",
            what=f"{self.label} attaching markdown to {to_path!r}",
            files={
                "path": (None, to_path),
                "data": ("content.md", text.encode(), "text/markdown"),
            },
        )

    async def detaches_markdown(self, *, from_path: str, in_pod: JSON) -> None:
        await self.api.delete(
            f"/pods/{in_pod['id']}/datastore/files/by-path/markdown",
            params={"path": from_path},
            # Detaching removes the derived text; the original file stays.
            what=f"{self.label} detaching markdown from {from_path!r}",
        )

    async def reads_derived_markdown(self, path: str, *, in_pod: JSON) -> Any:
        """The extracted or supplied markdown for a document.

        Takes the *child's* path — `/report.pdf/document.md` — not the parent's.
        Asking for the parent answers "no document child at /report.pdf", which
        reads like the markdown is missing when it is simply somewhere else.
        """
        return await self.api.call(
            "GET",
            f"/pods/{in_pod['id']}/datastore/files/children/content",
            params={"path": f"{path.rstrip('/')}/document.md"},
        )

    # --- watching records change ------------------------------------------

    def changes_url(self, pod: JSON, *, since: str | None = None) -> str:
        """Where a client watches a pod's records change.

        A websocket, because "as it happens" is the promise and polling cannot
        keep it. The session goes in the query string: a browser cannot set a
        header on a websocket handshake, so this is the shape a real client
        uses too.
        """
        base = self.api.base_url.replace("https://", "wss://").replace("http://", "ws://")
        url = f"{base}/pods/{pod['id']}/datastore/changes?access_token={self.api.token}"
        return f"{url}&since={since}" if since else url
