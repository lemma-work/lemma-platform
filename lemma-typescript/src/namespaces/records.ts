import type { GeneratedClientAdapter } from "../generated.js";
import type { BulkCreateRecordsRequest } from "../openapi_client/models/BulkCreateRecordsRequest.js";
import type { BulkDeleteRecordsRequest } from "../openapi_client/models/BulkDeleteRecordsRequest.js";
import type { BulkUpdateRecordsRequest } from "../openapi_client/models/BulkUpdateRecordsRequest.js";
import { RecordsService } from "../openapi_client/services/RecordsService.js";
import type { ListRecordsOptions, RecordFilter, RecordSort } from "../types.js";

export interface RecordQueryRequest {
  filters?: RecordFilter[];
  sort?: RecordSort[];
  limit?: number;
  page_token?: string;
  offset?: number;
}

function serializeFilters(filters?: RecordFilter[]): string[] | undefined {
  if (!filters || filters.length === 0) {
    return undefined;
  }
  return filters.map((filter) => JSON.stringify(filter));
}

function serializeSort(sort?: RecordSort[]): string[] | undefined {
  if (!sort || sort.length === 0) {
    return undefined;
  }
  return sort.map((entry) => JSON.stringify(entry));
}

export class RecordsNamespace {
  constructor(
    private readonly client: GeneratedClientAdapter,
    private readonly podId: () => string,
  ) {}

  list(table: string, options: ListRecordsOptions = {}) {
    const { filters, sort, limit, pageToken, offset } = options;

    return this.client.request(() =>
      RecordsService.recordList(
        this.podId(),
        table,
        limit ?? 20,
        offset,
        serializeFilters(filters),
        serializeSort(sort),
        pageToken,
      ),
    );
  }

  create(table: string, data: Record<string, unknown>) {
    return this.client.request(() => RecordsService.recordCreate(this.podId(), table, { data }));
  }

  get(table: string, recordId: string) {
    return this.client.request(() => RecordsService.recordGet(this.podId(), table, recordId));
  }

  update(table: string, recordId: string, data: Record<string, unknown>) {
    return this.client.request(() => RecordsService.recordUpdate(this.podId(), table, recordId, { data }));
  }

  delete(table: string, recordId: string) {
    return this.client.request(() => RecordsService.recordDelete(this.podId(), table, recordId));
  }

  query(table: string, payload: RecordQueryRequest) {
    return this.client.request(() => RecordsService.recordList(
      this.podId(),
      table,
      payload.limit ?? 20,
      payload.offset,
      serializeFilters(payload.filters),
      serializeSort(payload.sort),
      payload.page_token,
    ));
  }

  readonly bulk = {
    // `upsert` is what makes a bulk create idempotent: rows that conflict on
    // the table's primary key are updated rather than failing the request,
    // which is what re-seeding needs. The endpoint and the Python SDK have
    // always accepted it; only this wrapper dropped it.
    create: (table: string, records: Record<string, unknown>[], options: { upsert?: boolean } = {}) => {
      const payload: BulkCreateRecordsRequest = { records, upsert: options.upsert ?? false };
      return this.client.request(() => RecordsService.recordBulkCreate(this.podId(), table, payload));
    },

    update: (table: string, records: Record<string, unknown>[]) => {
      const payload: BulkUpdateRecordsRequest = { records };
      return this.client.request(() => RecordsService.recordBulkUpdate(this.podId(), table, payload));
    },

    delete: (table: string, recordIds: Array<string | number>) => {
      const payload: BulkDeleteRecordsRequest = { record_ids: recordIds };
      return this.client.request(() => RecordsService.recordBulkDelete(this.podId(), table, payload));
    },
  };
}
