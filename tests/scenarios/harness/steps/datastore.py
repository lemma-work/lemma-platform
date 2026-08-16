"""What a person does with tables, records, and files in a pod."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

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
        name = named or f"table_{uuid4().hex[:10]}"
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
                "name": named or f"table_{uuid4().hex[:10]}",
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
