import {
  resolveConfig,
  type LemmaAppConfig,
  type LemmaConfig,
} from "./config.js";
import { AuthManager, type AuthState, type AuthListener } from "./auth.js";
import { GeneratedClientAdapter } from "./generated.js";
import { HttpClient } from "./http.js";
import { AgentRuntimeNamespace } from "./namespaces/agent-runtime.js";
import { AgentsNamespace } from "./namespaces/agents.js";
import { ConversationsNamespace } from "./namespaces/conversations.js";
import { AppsNamespace } from "./namespaces/apps.js";
import { FilesNamespace } from "./namespaces/files.js";
import { FunctionsNamespace } from "./namespaces/functions.js";
import { IconsNamespace } from "./namespaces/icons.js";
import { ConnectorsNamespace } from "./namespaces/connectors.js";
import { OrganizationsNamespace } from "./namespaces/organizations.js";
import { PodMembersNamespace } from "./namespaces/pod-members.js";
import { PodPermissionsNamespace } from "./namespaces/pod-permissions.js";
import { PodJoinRequestsNamespace } from "./namespaces/pod-join-requests.js";
import { PodsNamespace } from "./namespaces/pods.js";
import { PodRolesNamespace } from "./namespaces/pod-roles.js";
import { PodSurfacesNamespace } from "./namespaces/pod-surfaces.js";
import { UserSurfacesNamespace } from "./namespaces/user-surfaces.js";
import { RecordsNamespace } from "./namespaces/records.js";
import { ResourceAccessNamespace } from "./namespaces/resource-access.js";
import { SchedulesNamespace } from "./namespaces/schedules.js";
import { TablesNamespace } from "./namespaces/tables.js";
import { UsersNamespace } from "./namespaces/users.js";
import { WorkflowsNamespace } from "./namespaces/workflows.js";
import { WidgetsNamespace } from "./namespaces/widgets.js";
import { DatastoreNamespace } from "./namespaces/datastore.js";

export type { LemmaConfig };
export type { LemmaAppConfig };
export { AuthManager };
export type { AuthState, AuthListener };

interface LemmaClientInternalOptions {
  authManager?: AuthManager;
}

export class LemmaClient {
  private readonly _config: LemmaConfig;
  private readonly _podId: string | undefined;
  private _currentPodId: string | undefined;

  /** Auth manager — subscribe to auth state, check auth, redirect to auth. */
  readonly auth: AuthManager;

  private readonly _http: HttpClient;
  private readonly _generated: GeneratedClientAdapter;

  // Namespaces
  readonly tables: TablesNamespace;
  readonly records: RecordsNamespace;
  readonly files: FilesNamespace;
  readonly functions: FunctionsNamespace;
  readonly agents: AgentsNamespace;
  readonly agentRuntime: AgentRuntimeNamespace;
  readonly conversations: ConversationsNamespace;
  readonly workflows: WorkflowsNamespace;
  readonly apps: AppsNamespace;
  readonly widgets: WidgetsNamespace;
  readonly connectors: ConnectorsNamespace;
  readonly resourceAccess: ResourceAccessNamespace;
  readonly schedules: SchedulesNamespace;
  readonly datastore: DatastoreNamespace;
  /** Alias of {@link datastore}, matching the Python SDK's `pod.queries`. */
  readonly queries: DatastoreNamespace;

  readonly users: UsersNamespace;
  readonly icons: IconsNamespace;
  readonly pods: PodsNamespace;
  readonly podMembers: PodMembersNamespace;
  readonly podPermissions: PodPermissionsNamespace;
  readonly podJoinRequests: PodJoinRequestsNamespace;
  readonly podRoles: PodRolesNamespace;
  readonly organizations: OrganizationsNamespace;
  readonly podSurfaces: PodSurfacesNamespace;
  /** The caller's own surfaces across all pods (grouped by platform). */
  readonly userSurfaces: UserSurfacesNamespace;

  constructor(
    overrides: Partial<LemmaConfig> = {},
    internalOptions: LemmaClientInternalOptions = {},
  ) {
    this._config = resolveConfig(overrides);
    this._currentPodId = this._config.podId;
    this._podId = this._config.podId;

    this.auth = internalOptions.authManager ?? new AuthManager(this._config.apiUrl, this._config.authUrl);
    this._http = new HttpClient(this._config.apiUrl, this.auth, {
      timeoutMs: this._config.timeoutMs,
      maxRetries: this._config.maxRetries,
    });
    this._generated = new GeneratedClientAdapter(this._config.apiUrl, this.auth, {
      maxRetries: this._config.maxRetries,
      timeoutMs: this._config.timeoutMs,
    });

    const podIdFn = () => {
      if (!this._currentPodId) {
        throw new Error(
          "pod_id is required. Pass podId in the constructor or call client.setPodId(id).",
        );
      }
      return this._currentPodId;
    };

    this.tables = new TablesNamespace(this._generated, podIdFn);
    this.records = new RecordsNamespace(this._generated, podIdFn);
    this.files = new FilesNamespace(this._generated, this._http, podIdFn);
    this.functions = new FunctionsNamespace(this._generated, podIdFn);
    this.agents = new AgentsNamespace(this._generated, podIdFn, () => this.conversations);
    this.agentRuntime = new AgentRuntimeNamespace(this._generated);
    this.conversations = new ConversationsNamespace(this._http, podIdFn);
    this.workflows = new WorkflowsNamespace(this._generated, this._http, podIdFn);
    this.apps = new AppsNamespace(this._generated, this._http, podIdFn);
    this.widgets = new WidgetsNamespace(this._http, podIdFn);
    this.connectors = new ConnectorsNamespace(this._generated, this._http);
    this.resourceAccess = new ResourceAccessNamespace(this._generated, podIdFn);
    this.schedules = new SchedulesNamespace(this._generated, podIdFn);
    this.datastore = new DatastoreNamespace(
      this._generated,
      podIdFn,
      this._config.apiUrl,
      this.auth,
    );
    this.queries = this.datastore;

    this.users = new UsersNamespace(this._generated);
    this.icons = new IconsNamespace(this._generated);
    this.pods = new PodsNamespace(this._generated, this._http);
    this.podMembers = new PodMembersNamespace(this._generated);
    this.podPermissions = new PodPermissionsNamespace(this._generated, this._http, podIdFn);
    this.podJoinRequests = new PodJoinRequestsNamespace(this._generated);
    this.podRoles = new PodRolesNamespace(this._generated, podIdFn);
    this.organizations = new OrganizationsNamespace(this._generated, this._http);
    this.podSurfaces = new PodSurfacesNamespace(this._generated);
    this.userSurfaces = new UserSurfacesNamespace(this._generated);
  }

  /** Change the active pod ID for subsequent calls. */
  setPodId(podId: string): void {
    this._currentPodId = podId;
  }

  /** Return a new client scoped to a specific pod, sharing auth state. */
  withPod(podId: string): LemmaClient {
    return new LemmaClient({ ...this._config, podId }, { authManager: this.auth });
  }

  get podId(): string | undefined {
    return this._currentPodId;
  }

  get apiUrl(): string {
    return this._config.apiUrl;
  }

  get authUrl(): string {
    return this._config.authUrl;
  }

  get app(): LemmaAppConfig | undefined {
    return this._config.app;
  }

  /**
   * Initialize the client by checking auth state.
   * Call this once on app startup (or let AuthGuard handle it).
   */
  async initialize(): Promise<AuthState> {
    return this.auth.checkAuth();
  }

  /** Raw HTTP request — escape hatch for operations not covered by namespaces. */
  request<T = unknown>(
    method: string,
    path: string,
    options?: {
      params?: Record<string, string | number | boolean | undefined | null>;
      body?: unknown;
      headers?: HeadersInit;
      isFormData?: boolean;
      signal?: AbortSignal;
    },
  ): Promise<T> {
    return this._http.request<T>(method, path, options);
  }

  /**
   * Raw streaming request — escape hatch for `text/event-stream` (SSE) endpoints
   * not covered by a namespace. Returns the response body; pair with `readSSE`.
   */
  stream(
    path: string,
    options?: {
      method?: "GET" | "POST" | "PATCH";
      params?: Record<string, string | number | boolean | undefined | null>;
      body?: unknown;
      headers?: HeadersInit;
      signal?: AbortSignal;
    },
  ): Promise<ReadableStream<Uint8Array>> {
    return this._http.stream(path, options);
  }
}
