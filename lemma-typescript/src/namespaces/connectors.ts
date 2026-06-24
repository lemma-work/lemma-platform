import type { GeneratedClientAdapter } from "../generated.js";
import type { HttpClient } from "../http.js";
import type { ConnectRequestInitiateSchema } from "../openapi_client/models/ConnectRequestInitiateSchema.js";
import type { ConnectRequestResponseSchema } from "../openapi_client/models/ConnectRequestResponseSchema.js";
import type { OperationDetailsBatchRequest } from "../openapi_client/models/OperationDetailsBatchRequest.js";
import type { OperationExecutionRequest } from "../openapi_client/models/OperationExecutionRequest.js";
import type { AccountCreateSchema } from "../openapi_client/models/AccountCreateSchema.js";
import type { AccountCredentialsResponseSchema } from "../openapi_client/models/AccountCredentialsResponseSchema.js";
import type { AccountListResponseSchema } from "../openapi_client/models/AccountListResponseSchema.js";
import type { AccountResponseSchema } from "../openapi_client/models/AccountResponseSchema.js";
import type { AuthConfigCreateSchema } from "../openapi_client/models/AuthConfigCreateSchema.js";
import type { AuthConfigListResponseSchema } from "../openapi_client/models/AuthConfigListResponseSchema.js";
import type { AuthConfigResponseSchema } from "../openapi_client/models/AuthConfigResponseSchema.js";
import type { MessageResponseSchema } from "../openapi_client/models/MessageResponseSchema.js";
import { ConnectorsService } from "../openapi_client/services/ConnectorsService.js";

export type {
  AuthConfigCreateSchema,
  AuthConfigListResponseSchema,
  AuthConfigResponseSchema,
};

type ConnectRequestInput = string | ConnectRequestInitiateSchema;
type OperationScope = {
  organizationId: string;
  authConfigName: string;
};
type EnableAppOptions = Omit<AuthConfigCreateSchema, "connector_id" | "credential_config"> & {
  credential_config?: Record<string, unknown> | null;
  provider_config?: Record<string, unknown> | null;
};

function encodePath(value: string): string {
  return encodeURIComponent(value);
}

export class ConnectorsNamespace {
  constructor(
    private readonly client: GeneratedClientAdapter,
    private readonly http: HttpClient,
  ) {}

  list(options: { limit?: number; pageToken?: string } = {}) {
    return this.client.request(() => ConnectorsService.connectorList(options.limit ?? 100, options.pageToken));
  }
  get(connectorId: string) {
    return this.client.request(() => ConnectorsService.connectorGet(connectorId));
  }

  readonly operations = {
    discover: (scope: OperationScope, options: { query?: string; limit?: number } = {}) =>
      this.client.request(() => ConnectorsService.connectorOperationDiscover(
        scope.organizationId,
        scope.authConfigName,
        options.query,
        options.limit ?? 100,
      )),
    list: async (scope: OperationScope, options: { query?: string; limit?: number } = {}) => {
      const response = await this.client.request(() => ConnectorsService.connectorOperationDiscover(
        scope.organizationId,
        scope.authConfigName,
        options.query,
        options.limit ?? 100,
      ));
      return response.items ?? [];
    },
    get: (scope: OperationScope, operationName: string) =>
      this.client.request(() => ConnectorsService.connectorOperationDetail(
        scope.organizationId,
        scope.authConfigName,
        operationName,
      )),
    details: (scope: OperationScope, operationNames?: string[]) => {
      const body: OperationDetailsBatchRequest = { operation_names: operationNames };
      return this.client.request(() => ConnectorsService.connectorOperationDetailsBatch(
        scope.organizationId,
        scope.authConfigName,
        body,
      ));
    },
    execute: (scope: OperationScope, operationName: string, payload: Record<string, unknown>, accountId?: string) => {
      const body: OperationExecutionRequest = { payload, account_id: accountId };
      return this.client.request(() => ConnectorsService.connectorOperationExecute(
        scope.organizationId,
        scope.authConfigName,
        operationName,
        body,
      ));
    },
  };

  readonly triggers = {
    list: (scope: OperationScope, options: { search?: string; limit?: number } = {}) =>
      this.client.request(() => ConnectorsService.connectorTriggerList(
        scope.organizationId,
        scope.authConfigName,
        options.search,
        options.limit ?? 100,
      )),
    get: (scope: OperationScope, triggerName: string) =>
      this.client.request(() => ConnectorsService.connectorTriggerGet(
        scope.organizationId,
        scope.authConfigName,
        triggerName,
      )),
  };

  readonly accounts = {
    list: (organizationId: string, options: { connectorId?: string; limit?: number; pageToken?: string } = {}) =>
      this.client.request(() => ConnectorsService.connectorAccountList(
        organizationId,
        options.connectorId,
        options.limit ?? 100,
        options.pageToken,
      )),
    create: (organizationId: string, payload: AccountCreateSchema) =>
      this.client.request(() => ConnectorsService.connectorAccountCreate(
        organizationId,
        payload,
      )),
    get: (organizationId: string, accountId: string) =>
      this.client.request(() => ConnectorsService.connectorAccountGet(
        organizationId,
        accountId,
      )),
    credentials: (organizationId: string, accountId: string) =>
      this.client.request(() => ConnectorsService.connectorAccountCredentialsGet(
        organizationId,
        accountId,
      )),
    delete: (organizationId: string, accountId: string) =>
      this.client.request(() => ConnectorsService.connectorAccountDelete(
        organizationId,
        accountId,
      )),
    /**
     * @deprecated Use list/get/create with an organization id. Kept only for
     * callers that still need the response shape while migrating.
     */
    listOrgScoped: (organizationId: string, options: { connectorId?: string; limit?: number; pageToken?: string } = {}) =>
      this.http.request<AccountListResponseSchema>(
        "GET",
        `/organizations/${encodePath(organizationId)}/connectors/accounts`,
        {
          params: {
            connector_id: options.connectorId,
            limit: options.limit ?? 100,
            page_token: options.pageToken,
          },
        },
      ),
  };

  readonly authConfigs = {
    list: (organizationId: string, options: { limit?: number; pageToken?: string } = {}) =>
      this.client.request(() => ConnectorsService.connectorAuthConfigList(
        organizationId,
        options.limit ?? 100,
        options.pageToken,
      )),
    create: (organizationId: string, payload: AuthConfigCreateSchema) =>
      this.client.request(() => ConnectorsService.connectorAuthConfigCreate(
        organizationId,
        payload,
      )),
    get: (organizationId: string, authConfigName: string) =>
      this.client.request(() => ConnectorsService.connectorAuthConfigGet(
        organizationId,
        authConfigName,
      )),
    delete: (organizationId: string, authConfigName: string) =>
      this.client.request(() => ConnectorsService.connectorAuthConfigDelete(
        organizationId,
        authConfigName,
      )),
  };

  async enableApp(
    organizationId: string,
    connectorId: string,
    options: EnableAppOptions = {},
  ) {
    const configs = await this.authConfigs.list(organizationId, { limit: 100 });
    const existing = configs.items.find((config) => config.connector_id === connectorId && config.status === "ACTIVE");
    if (existing) return existing;

    return this.authConfigs.create(organizationId, {
      connector_id: connectorId,
      provider: options.provider,
      config_source: options.config_source ?? "SYSTEM_DEFAULT",
      credential_config: options.credential_config ?? options.provider_config,
      name: options.name,
    });
  }

  createConnectRequest(organizationId: string, input: ConnectRequestInput) {
    const payload: ConnectRequestInitiateSchema =
      typeof input === "string" ? { connector_id: input } : input;
    return this.client.request(() => ConnectorsService.connectorConnectRequestCreate(organizationId, payload));
  }
}
