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

  /**
   * One page of a table's rows. `limit` defaults to 20 and the rest is behind
   * `next_page_token`; see {@link listAll} when the answer has to be complete.
   */
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

  /**
   * Every matching row, paged to exhaustion.
   *
   * "Read a table" and "read all of a table" are different operations and the
   * difference is invisible at the call site, so a default page of 20 makes a
   * partial read look complete. The filter and sort are re-sent with every
   * page, so the walk cannot widen halfway through.
   */
  async listAll(
    table: string,
    options: Omit<ListRecordsOptions, "limit" | "pageToken"> & { pageSize?: number } = {},
  ): Promise<Record<string, unknown>[]> {
    const { filters, sort, offset, pageSize } = options;
    const rows: Record<string, unknown>[] = [];
    let pageToken: string | undefined;

    for (;;) {
      const page = await this.list(table, {
        filters,
        sort,
        offset,
        limit: pageSize ?? 500,
        pageToken,
      });
      rows.push(...(page.items ?? []));
      pageToken = page.next_page_token ?? undefined;
      if (!pageToken) {
        return rows;
      }
    }
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
