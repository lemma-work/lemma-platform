import type { GeneratedClientAdapter } from "../generated.js";
import type { HttpClient } from "../http.js";
import type { ConnectRequestInitiateSchema } from "../openapi_client/models/ConnectRequestInitiateSchema.js";
import type { ConnectRequestResponseSchema } from "../openapi_client/models/ConnectRequestResponseSchema.js";
import type { OperationDetailsBatchRequest } from "../openapi_client/models/OperationDetailsBatchRequest.js";
import type { OperationExecutionRequest } from "../openapi_client/models/OperationExecutionRequest.js";
import type { AccountCreateSchema } from "../openapi_client/models/AccountCreateSchema.js";
import type { AccountListResponseSchema } from "../openapi_client/models/AccountListResponseSchema.js";
import type { AccountResponseSchema } from "../openapi_client/models/AccountResponseSchema.js";
import type { AuthConfigCreateSchema } from "../openapi_client/models/AuthConfigCreateSchema.js";
import type { AuthConfigListResponseSchema } from "../openapi_client/models/AuthConfigListResponseSchema.js";
import type { AuthConfigResponseSchema } from "../openapi_client/models/AuthConfigResponseSchema.js";
import type { AuthConfigUpdateSchema } from "../openapi_client/models/AuthConfigUpdateSchema.js";
import type { MessageResponseSchema } from "../openapi_client/models/MessageResponseSchema.js";
import { ConnectorsService } from "../openapi_client/services/ConnectorsService.js";

export type {
  AuthConfigCreateSchema,
  AuthConfigListResponseSchema,
  AuthConfigResponseSchema,
  AuthConfigUpdateSchema,
};

type ConnectRequestInput = string | ConnectRequestInitiateSchema;
type OperationScope = {
  organizationId: string;
  authConfigName: string;
};
type EnableAppOptions = Omit<AuthConfigCreateSchema, "connector_id" | "config"> & {
  config?: Record<string, unknown> | null;
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
    // Updates an install in place. Rotating a server URL or an OAuth app this
    // way keeps the accounts attached to it; delete-and-recreate cascades them
    // away. Accounts whose credentials the change invalidates are marked for
    // reconnect, never deleted, and the response says how many.
    update: (organizationId: string, authConfigName: string, payload: AuthConfigUpdateSchema) =>
      this.client.request(() => ConnectorsService.connectorAuthConfigUpdate(
        organizationId,
        authConfigName,
        payload,
      )),
    // Re-discovers an MCP or OpenAPI install's operations. The recovery path:
    // deleting and recreating the install cascades away its accounts.
    refreshOperations: (organizationId: string, authConfigName: string) =>
      this.client.request(() => ConnectorsService.connectorAuthConfigRefreshOperations(
        organizationId,
        authConfigName,
      )),
  };

  /**
   * Enable a connector for an organization, reusing an existing install only
   * when the caller has described nothing that would distinguish a new one.
   *
   * This used to return ANY active install with a matching connector id,
   * ignoring the submitted kind, config and name. The backend deliberately
   * permits many installs of one connector -- see the comment on the
   * `auth_configs` model, which says there is deliberately no
   * `(organization_id, connector_id)` uniqueness -- so this was the SDK
   * enforcing a constraint the schema had dropped, client-side and silently.
   *
   * Two things it broke. Every MCP server shares the catalog id `mcp`, every
   * database shares `sql`, every REST API shares `openapi`: a second one
   * returned the first, and the caller was told it worked. And choosing "use
   * my own credentials" for a connector the org already had returned the
   * Lemma-managed install, dropping the submitted client id and secret, so
   * OAuth then ran against Lemma's app rather than theirs.
   */
  async enableApp(
    organizationId: string,
    connectorId: string,
    options: EnableAppOptions = {},
  ) {
    // A name, a config, or bringing your own credentials all describe a
    // particular install rather than "make sure this connector is on".
    const describesAParticularInstall = Boolean(
      options.name || options.config || options.config_source === "ORG_CUSTOM",
    );
    if (!describesAParticularInstall) {
      const configs = await this.authConfigs.list(organizationId, { limit: 100 });
      // `kind` narrows the match rather than forcing a create: "enable gmail
      // as composio" should still reuse an existing composio install. But it
      // must narrow, because a connector can ship several kinds -- choosing
      // "Native OAuth" in Advanced setup for an org already holding a Composio
      // install used to return that install, and the caller then read the kind
      // back off the returned row and ran the Composio flow, having been told
      // it enabled the one they picked.
      const candidates = configs.items.filter((config) =>
        config.connector_id === connectorId
        && config.status === "ACTIVE"
        && (!options.kind || config.kind === options.kind),
      );
      // The default is the install a bare connector id resolves to everywhere
      // else -- `findDefaultInstallName` in the frontend, and the backend's own
      // `uq_auth_configs_default_per_connector`. Taking the first row in list
      // order instead made this the one place that disagreed.
      const existing = candidates.find((config) => config.is_default) ?? candidates[0];
      if (existing) return existing;
    }

    return this.authConfigs.create(organizationId, {
      connector_id: connectorId,
      kind: options.kind,
      config_source: options.config_source ?? "SYSTEM_DEFAULT",
      config: options.config,
      name: options.name,
    });
  }

  createConnectRequest(organizationId: string, input: ConnectRequestInput) {
    const payload: ConnectRequestInitiateSchema =
      typeof input === "string" ? { connector_id: input } : input;
    return this.client.request(() => ConnectorsService.connectorConnectRequestCreate(organizationId, payload));
  }
}
