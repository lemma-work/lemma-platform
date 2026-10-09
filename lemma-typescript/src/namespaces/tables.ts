import type { GeneratedClientAdapter } from "../generated.js";
import type { AddColumnRequest } from "../openapi_client/models/AddColumnRequest.js";
import type { CreateTableRequest } from "../openapi_client/models/CreateTableRequest.js";
import type { OpenTableRequest } from "../openapi_client/models/OpenTableRequest.js";
import type { UpdateTableRequest } from "../openapi_client/models/UpdateTableRequest.js";
import { TablesService } from "../openapi_client/services/TablesService.js";

type LegacyCreateTableRequest = Omit<CreateTableRequest, "name"> & {
  table_name: string;
  name?: string;
};

export type CreateTableInput = CreateTableRequest | LegacyCreateTableRequest;

function normalizeCreateTablePayload(payload: CreateTableInput): CreateTableRequest {
  if ("table_name" in payload) {
    const { table_name, name, ...rest } = payload;
    return {
      ...rest,
      name: name ?? table_name,
    };
  }

  return payload;
}

export class TablesNamespace {
  constructor(private readonly client: GeneratedClientAdapter, private readonly podId: () => string) {}

  list(options: { limit?: number; pageToken?: string } = {}) {
    return this.client.request(() => TablesService.tableList(this.podId(), options.limit ?? 100, options.pageToken));
  }

  create(payload: CreateTableInput) {
    return this.client.request(() => TablesService.tableCreate(this.podId(), normalizeCreateTablePayload(payload)));
  }

  get(tableName: string) {
    return this.client.request(() => TablesService.tableGet(this.podId(), tableName));
  }

  update(tableName: string, payload: UpdateTableRequest) {
    return this.client.request(() => TablesService.tableUpdate(this.podId(), tableName, payload));
  }

  delete(tableName: string) {
    return this.client.request(() => TablesService.tableDelete(this.podId(), tableName));
  }

  readonly columns = {
    add: (tableName: string, request: AddColumnRequest | AddColumnRequest["column"]) => {
      const payload: AddColumnRequest = "column" in request ? request : { column: request };
      return this.client.request(() => TablesService.tableColumnAdd(this.podId(), tableName, payload));
    },

    remove: (tableName: string, columnName: string) =>
      this.client.request(() => TablesService.tableColumnRemove(this.podId(), tableName, columnName)),
  };

  /**
   * Who outside the pod may add rows to a table. A form is any page that adds a
   * row through a web widget's key; the table decides which columns it may write.
   */
  readonly publicRows = {
    get: (tableName: string) =>
      this.client.request(() => TablesService.tablePublicRowsGet(this.podId(), tableName)),
    open: (tableName: string, payload: OpenTableRequest) =>
      this.client.request(() => TablesService.tablePublicRowsOpen(this.podId(), tableName, payload)),
    close: (tableName: string) =>
      this.client.request(() => TablesService.tablePublicRowsClose(this.podId(), tableName)),
    list: () => this.client.request(() => TablesService.tablePublicRowsList(this.podId())),
  };
}
