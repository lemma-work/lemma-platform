# datastore contract

What every `datastore` API operation guarantees: who may call it, what must be true first, what changes, what it emits, and how it refuses.

The product promises these serve are in [the product specification](../../../docs/product/README.md). This says what each operation does; that says what any of it is for.

The table below is generated from the committed OpenAPI specification by `scripts/check_contracts.py --write`. Add the behaviour in prose under each operation's heading, outside the generated block — that part is preserved across regeneration.

<!-- generated:operations -- do not edit below -->

| Operation | Method | Path | Summary |
| --- | --- | --- | --- |
| `file.child.get` | GET | `/pods/{pod_id}/datastore/files/children/content` | Fetch a document's child artifact by path |
| `file.children.list` | GET | `/pods/{pod_id}/datastore/files/children` | List a document's derived child files |
| `file.delete` | DELETE | `/pods/{pod_id}/datastore/files/by-path` | Delete File Or Folder |
| `file.download` | GET | `/pods/{pod_id}/datastore/files/download` | Download File |
| `file.folder.create` | POST | `/pods/{pod_id}/datastore/files/folders` | Create Folder |
| `file.get` | GET | `/pods/{pod_id}/datastore/files/by-path` | Get File |
| `file.get_by_id` | GET | `/pods/{pod_id}/datastore/files/{file_id}` | Get File by ID |
| `file.list` | GET | `/pods/{pod_id}/datastore/files` | List Files |
| `file.markdown.attach` | PUT | `/pods/{pod_id}/datastore/files/by-path/markdown` | Attach Document Markdown |
| `file.markdown.detach` | DELETE | `/pods/{pod_id}/datastore/files/by-path/markdown` | Detach Document Markdown |
| `file.search` | POST | `/pods/{pod_id}/datastore/files/search` | Search Files |
| `file.signed_url` | POST | `/pods/{pod_id}/datastore/files/signed-url` | Create a public, hit-capped signed URL for a file |
| `file.tree` | GET | `/pods/{pod_id}/datastore/files/tree` | Get Directory Tree |
| `file.update` | PATCH | `/pods/{pod_id}/datastore/files/by-path` | Update File |
| `file.upload` | POST | `/pods/{pod_id}/datastore/files` | Upload File |
| `file.url` | GET | `/pods/{pod_id}/datastore/files/url` | Get a short-lived URL for a file |
| `query.execute` | POST | `/pods/{pod_id}/datastore/query` | Execute Query |
| `record.bulk_create` | POST | `/pods/{pod_id}/datastore/tables/{table_name}/records/bulk/create` | Bulk Create |
| `record.bulk_delete` | POST | `/pods/{pod_id}/datastore/tables/{table_name}/records/bulk/delete` | Bulk Delete |
| `record.bulk_update` | POST | `/pods/{pod_id}/datastore/tables/{table_name}/records/bulk/update` | Bulk Update |
| `record.create` | POST | `/pods/{pod_id}/datastore/tables/{table_name}/records` | Create Record |
| `record.delete` | DELETE | `/pods/{pod_id}/datastore/tables/{table_name}/records/{record_id}` | Delete Record |
| `record.get` | GET | `/pods/{pod_id}/datastore/tables/{table_name}/records/{record_id}` | Get Record |
| `record.list` | GET | `/pods/{pod_id}/datastore/tables/{table_name}/records` | List Records |
| `record.update` | PATCH | `/pods/{pod_id}/datastore/tables/{table_name}/records/{record_id}` | Update Record |
| `table.column.add` | POST | `/pods/{pod_id}/datastore/tables/{table_name}/columns` | Add Column |
| `table.column.remove` | DELETE | `/pods/{pod_id}/datastore/tables/{table_name}/columns/{column_name}` | Remove Column |
| `table.create` | POST | `/pods/{pod_id}/datastore/tables` | Create Table |
| `table.delete` | DELETE | `/pods/{pod_id}/datastore/tables/{table_name}` | Delete Table |
| `table.get` | GET | `/pods/{pod_id}/datastore/tables/{table_name}` | Get Table |
| `table.list` | GET | `/pods/{pod_id}/datastore/tables` | List Tables |
| `table.update` | PATCH | `/pods/{pod_id}/datastore/tables/{table_name}` | Update Table |

<!-- /generated:operations -->
