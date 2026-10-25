import type { GeneratedClientAdapter } from "../generated.js";
import type { HttpClient } from "../http.js";
import type { ConnectRequestInitiateSchema } from "../openapi_client/models/ConnectRequestInitiateSchema.js";
import type { ConnectRequestResponseSchema } from "../openapi_client/models/ConnectRequestResponseSchema.js";
import type { OperationDetailsBatchRequest } from "../openapi_client/models/OperationDetailsBatchRequest.js";
import type { OperationExecutionRequest } from "../openapi_client/models/OperationExecutionRequest.js";
import type { OperationExecutionResponse } from "../openapi_client/models/OperationExecutionResponse.js";
import type { PodAccountCreateRequest } from "../openapi_client/models/PodAccountCreateRequest.js";
import type { PodAccountUpdateRequest } from "../openapi_client/models/PodAccountUpdateRequest.js";
import type { PodConnectorCreateRequest } from "../openapi_client/models/PodConnectorCreateRequest.js";
import type { PodConnectorUpdateRequest } from "../openapi_client/models/PodConnectorUpdateRequest.js";
import type { PodConnectorResponse } from "../openapi_client/models/PodConnectorResponse.js";
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
/** An org installation, for discovering what it offers. Running an operation
 *  goes through a pod connector instead (`connectors.pod.execute`). */
type OperationScope = {
  organizationId: string;
  authConfigName: string;
  /** Deprecated with `operations.execute`: the pod whose connectors serve the
   *  call. Implied inside a pod. */
  podId?: string;
};
type EnableAppOptions = Omit<AuthConfigCreateSchema, "connector_id" | "config"> & {
  config?: Record<string, unknown> | null;
};

function encodePath(value: string): string {
  return encodeURIComponent(value);
}

/** A member a required per-member connector is still waiting on: who, and
 *  why -- never which account. */
export type PodConnectorMemberAwaited = {
  user_id: string;
  pod_member_id: string;
  name?: string | null;
  email?: string | null;
  standing: "CONNECTED" | "MISSING" | "REAUTH_REQUIRED";
};

/** How many of the members who may use a connector can run it as their own. */
export type PodConnectorCoverage = {
  eligible: number;
  connected: number;
  waiting_on: PodConnectorMemberAwaited[];
  truncated: boolean;
};

/** Who was asked to connect, and who was not asked again. */
export type AskMembersResponse = {
  asked: PodConnectorMemberAwaited[];
  asked_recently: PodConnectorMemberAwaited[];
  not_asked: PodConnectorMemberAwaited[];
  connect_url: string;
};

export class ConnectorsNamespace {
  constructor(
    private readonly client: GeneratedClientAdapter,
    private readonly http: HttpClient,
    private readonly podId: () => string,
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
    /**
     * @deprecated Use `connectors.pod.execute(slug, operationName, payload)`:
     * the pod connector decides the account, so drop `accountId`. Still served
     * -- through the connectors the pod declares for this installation -- and
     * each response says so, which this client logs once as a warning.
     */
    execute: (scope: OperationScope, operationName: string, payload: Record<string, unknown>, accountId?: string) => {
      const podId = scope.podId ?? this.currentPodId();
      return this.http.request<OperationExecutionResponse>(
        "POST",
        `/organizations/${encodePath(scope.organizationId)}/connectors/${encodePath(scope.authConfigName)}`
          + `/operations/${encodePath(operationName)}/execute`,
        {
          body: {
            payload,
            ...(accountId ? { account_id: accountId } : {}),
            ...(podId ? { pod_id: podId } : {}),
          },
        },
      );
    },
  };

  /** The client's pod when it has one; the deprecated execute works without. */
  private currentPodId(): string | undefined {
    try {
      return this.podId();
    } catch {
      return undefined;
    }
  }

  /**
   * The connectors this pod declares, addressed by slug. Running an operation
   * always goes through one: the pod connector decides whose account it runs
   * as (the caller's own, or the one the pod shares), so a call never names an
   * account. A missing or expired account comes back as a typed error carrying
   * `connect_url`, where the caller can fix it.
   */
  readonly pod = {
    list: () =>
      this.client.request(() => ConnectorsService.podConnectorList(this.podId())),
    get: (slug: string) =>
      this.client.request(() => ConnectorsService.podConnectorGet(this.podId(), slug)),
    create: (payload: PodConnectorCreateRequest) =>
      this.client.request(() => ConnectorsService.podConnectorCreate(this.podId(), payload)),
    update: (slug: string, payload: PodConnectorUpdateRequest) =>
      this.client.request(() => ConnectorsService.podConnectorUpdate(this.podId(), slug, payload)),
    delete: (slug: string) =>
      this.client.request(() => ConnectorsService.podConnectorDelete(this.podId(), slug)),
    /**
     * Who of the members allowed to use a required per-member connector has
     * connected an account, and who it is still waiting on. Needs
     * `pod.connector.manage`.
     */
    coverage: (slug: string) =>
      this.http.request<PodConnectorCoverage>(
        "GET",
        `/pods/${encodePath(this.podId())}/connectors/${encodePath(slug)}/coverage`,
      ),
    /**
     * Ask the members a per-member connector is waiting on to connect: one
     * line each in their own channel with the pod. Each member is asked about
     * one connector at most once a day; `asked_recently` names the rest.
     * Needs `pod.connector.manage`. `userIds` asks only those of them.
     */
    askMembers: (slug: string, options: { userIds?: string[] } = {}) =>
      this.http.request<AskMembersResponse>(
        "POST",
        `/pods/${encodePath(this.podId())}/connectors/${encodePath(slug)}/ask-members`,
        { body: options.userIds ? { user_ids: options.userIds } : {} },
      ),
    operations: async (slug: string, options: { query?: string; limit?: number } = {}) => {
      const response = await this.client.request(() => ConnectorsService.podConnectorOperationList(
        this.podId(),
        slug,
        options.query,
        options.limit ?? 100,
      ));
      return response.items ?? [];
    },
    operation: (slug: string, operationName: string) =>
      this.client.request(() => ConnectorsService.podConnectorOperationGet(
        this.podId(),
        slug,
        operationName,
      )),
    execute: (slug: string, operationName: string, payload: Record<string, unknown>) => {
      const body: OperationExecutionRequest = { payload };
      return this.client.request(() => ConnectorsService.podConnectorOperationExecute(
        this.podId(),
        slug,
        operationName,
        body,
      ));
    },
    accounts: {
      list: () =>
        this.client.request(() => ConnectorsService.podConnectorAccountList(this.podId())),
      add: (payload: PodAccountCreateRequest) =>
        this.client.request(() => ConnectorsService.podConnectorAccountAdd(this.podId(), payload)),
      update: (podAccountId: string, payload: PodAccountUpdateRequest) =>
        this.client.request(() => ConnectorsService.podConnectorAccountUpdate(
          this.podId(),
          podAccountId,
          payload,
        )),
      remove: (podAccountId: string) =>
        this.client.request(() => ConnectorsService.podConnectorAccountRemove(this.podId(), podAccountId)),
      /**
       * Share one of your own pod accounts as the SHARED connector `slug`,
       * in one step: the connector is pointed at it or declared, and the
       * account shared with the pod, or neither happens.
       */
      shareAs: (podAccountId: string, slug: string, options: { purpose?: string } = {}) =>
        this.http.request<PodConnectorResponse>(
          "POST",
          `/pods/${encodePath(this.podId())}/connector-accounts/${encodePath(podAccountId)}/share-as`,
          { body: { slug, ...(options.purpose ? { purpose: options.purpose } : {}) } },
        ),
    },
  };

  /**
   * Every pod one of my accounts is in: its visibility there, the SHARED pod
   * connectors that run as it, and when it was last used.
   */
  accountPods(accountId: string) {
    return this.client.request(() => ConnectorsService.connectorAccountPods(accountId));
  }

  /**
   * What removing an installation would stop -- its accounts and every pod
   * connector declared on it, and what runs through those -- for the
   * confirmation shown before it goes. Installation managers only.
   */
  installationImpact(organizationId: string, authConfigId: string) {
    return this.client.request(() => ConnectorsService.connectorInstallationImpact(organizationId, authConfigId));
  }

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

  /**
   * Replace a credential-managed account's credential, keeping its id.
   *
   * Deleting and reconnecting also rotates a credential, and issues a new
   * account id doing it — stranding every schedule, surface and grant pinned
   * to the old one, and leaving nothing behind at all if the reconnect fails.
   */
  rotateAccountCredentials(
    organizationId: string,
    accountId: string,
    credentials: Record<string, unknown>,
  ) {
    return this.client.request(() => ConnectorsService.connectorAccountUpdate(
      organizationId,
      accountId,
      { credentials } as never,
    ));
  }

  createConnectRequest(organizationId: string, input: ConnectRequestInput) {
    const payload: ConnectRequestInitiateSchema =
      typeof input === "string" ? { connector_id: input } : input;
    return this.client.request(() => ConnectorsService.connectorConnectRequestCreate(organizationId, payload));
  }

  /**
   * Where to send somebody who authorized but has not installed.
   *
   * A GitHub App's user token reaches only repositories the App is installed
   * on, so authorizing alone produces a working token that can read nothing.
   * The returned URL carries a single-use state that expires in thirty
   * minutes, so ask for it when the person is about to follow it.
   */
  createInstallRequest(organizationId: string, accountId: string, returnTo?: string) {
    return this.client.request(() => ConnectorsService.connectorConnectRequestInstall(
      organizationId,
      { account_id: accountId, return_to: returnTo },
    ));
  }

  /**
   * Which GitHub App installations an account can reach.
   *
   * `refresh` asks the provider again rather than trusting what is recorded:
   * editing an installation's repositories sends no callback and no reliable
   * event, so this is how a change made on GitHub is seen.
   */
  accountInstallations(organizationId: string, accountId: string, refresh = false) {
    return this.client.request(() => ConnectorsService.connectorAccountInstallations(
      organizationId,
      accountId,
      refresh,
    ));
  }

  /** Settle which installation an account speaks for, when it can reach several. */
  bindAccountInstallation(organizationId: string, accountId: string, installationId: string) {
    return this.client.request(() => ConnectorsService.connectorAccountBindInstallation(
      organizationId,
      accountId,
      { installation_id: installationId },
    ));
  }
}
