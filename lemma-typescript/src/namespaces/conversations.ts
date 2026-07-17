import type { HttpClient } from "../http.js";
import type { AgentHarnessListResponse } from "../openapi_client/models/AgentHarnessListResponse.js";
import type { ApprovalDecisionResponse } from "../openapi_client/models/ApprovalDecisionResponse.js";
import type { AgentRuntimeConfig } from "../openapi_client/models/AgentRuntimeConfig.js";
import type { AgentRuntimeProfileListResponse } from "../openapi_client/models/AgentRuntimeProfileListResponse.js";
import type { AgentRuntimeProfileResponse } from "../openapi_client/models/AgentRuntimeProfileResponse.js";
import type { ConversationListResponse } from "../openapi_client/models/ConversationListResponse.js";
import type { ConversationType } from "../openapi_client/models/ConversationType.js";
import type { CreateConversationRequest } from "../openapi_client/models/CreateConversationRequest.js";
import type { HarnessKind } from "../openapi_client/models/HarnessKind.js";
import type { ResolveUserApprovalRequest } from "../openapi_client/models/ResolveUserApprovalRequest.js";
import type { SendMessageRequest } from "../openapi_client/models/SendMessageRequest.js";
import type { UpdateConversationRequest } from "../openapi_client/models/UpdateConversationRequest.js";
import type { UserApprovalListResponse } from "../openapi_client/models/UserApprovalListResponse.js";
import type {
  AvailableModelInfo,
  Conversation,
  ConversationMessage,
  ConversationModel,
  CursorPage,
} from "../types.js";

export const POD_DEFAULT_AGENT_SELECTOR = "POD_DEFAULT" as const;

type ConversationCreateInput = CreateConversationRequest & {
  agent_runtime?: AgentRuntimeConfig | null;
  agent_name?: string | null;
  harness_kind?: HarnessKind | null;
  model?: ConversationModel | null;
  model_name?: ConversationModel | null;
  pod_id?: string | null;
  profile_id?: string | null;
};

type ConversationUpdateInput = UpdateConversationRequest & {
  agent_runtime?: AgentRuntimeConfig | null;
  harness_kind?: HarnessKind | null;
  model?: ConversationModel | null;
  model_name?: ConversationModel | null;
  profile_id?: string | null;
};

type UserApprovalDecision = ResolveUserApprovalRequest["decision"] | "APPROVE_ONCE" | "APPROVE_FOR_SESSION" | "DENY";

type ResolveUserApprovalInput = Omit<ResolveUserApprovalRequest, "decision"> & {
  decision: UserApprovalDecision;
};

function normalizeConversation<T extends Conversation | null>(conversation: T): T {
  if (!conversation) return conversation;
  const record = conversation as Conversation & { model_name?: ConversationModel | null };
  return {
    ...record,
    model: record.model ?? record.agent_runtime?.model_name ?? record.model_name ?? null,
    status: record.status ?? "waiting",
  } as T;
}

function normalizeConversationList(response: ConversationListResponse): CursorPage<Conversation> & ConversationListResponse {
  const items = (response.items ?? []).map((conversation) => normalizeConversation(conversation as Conversation));
  return {
    ...response,
    items,
  };
}

function normalizeMessage(message: ConversationMessage): ConversationMessage {
  return message;
}

export class ConversationsNamespace {
  private runtimeCatalogPromise: Promise<AgentHarnessListResponse> | undefined;
  private profileCatalogPromises = new Map<string, Promise<AgentRuntimeProfileListResponse>>();

  constructor(
    private readonly http: HttpClient,
    private readonly podId: () => string,
  ) {}

  private resolvePodId(explicitPodId?: string | null): string | undefined {
    if (typeof explicitPodId === "string") {
      return explicitPodId;
    }

    try {
      return this.podId();
    } catch {
      return undefined;
    }
  }

  private requirePodId(explicitPodId?: string | null): string {
    const podId = this.resolvePodId(explicitPodId);
    if (!podId) {
      throw new Error("pod_id is required for this conversation operation.");
    }
    return podId;
  }

  private listRuntimeCatalog(): Promise<AgentHarnessListResponse> {
    this.runtimeCatalogPromise ??= this.http.request<AgentHarnessListResponse>(
      "GET",
      "/agent-runtime/harnesses",
    );
    return this.runtimeCatalogPromise;
  }

  private listProfileCatalog(orgId: string): Promise<AgentRuntimeProfileListResponse> {
    const key = orgId.trim();
    const existing = this.profileCatalogPromises.get(key);
    if (existing) return existing;

    const request = this.http.request<AgentRuntimeProfileListResponse>(
      "GET",
      `/organizations/${encodeURIComponent(key)}/agent-runtime/profiles`,
    );
    this.profileCatalogPromises.set(key, request);
    return request;
  }

  private modelOptionsFromProfiles(catalog: AgentRuntimeProfileListResponse): AvailableModelInfo[] {
    return catalog.items.flatMap((profile) => {
      const catalogEntries = profile.model_catalog ?? [];
      const entries = catalogEntries.length > 0
        ? catalogEntries
        : profile.default_model_name
          ? [{
              name: profile.default_model_name,
              display_name: null,
              provider_model_name: profile.default_model_name,
              capabilities: [],
              default_model_settings: {},
              metadata: {},
            }]
          : [];

      return entries.map((model) => ({
        id: model.name as ConversationModel,
        name: model.display_name ?? model.name,
        agentRuntime: profile,
        agentRuntimeId: profile.id,
        profile,
        profile_id: profile.id,
        harness_kind: profile.derived_harness_kind,
        description: profile.name,
        runtime: {
          profile_id: profile.id,
          model_name: model.name,
        },
      }));
    });
  }

  private async resolveAgentRuntime(
    agentRuntime: AgentRuntimeConfig | null | undefined,
    model: ConversationModel | null | undefined,
    harnessKind: HarnessKind | null | undefined,
    profileId: string | null | undefined,
  ): Promise<AgentRuntimeConfig | null | undefined> {
    if (agentRuntime || !model) {
      return agentRuntime;
    }

    if (profileId) {
      return {
        profile_id: profileId,
        model_name: model,
      };
    }

    void harnessKind;
    return undefined;
  }

  list(options: {
    agent_name?: string | null;
    pod_id?: string | null;
    // Root conversations only by default; pass parent_id to fetch a
    // conversation's children (sub-agents or conversations pinned under a
    // PROJECT). `type` filters by CHAT / TASK / PROJECT and composes with it.
    parent_id?: string | null;
    type?: ConversationType | null;
    limit?: number;
    page_token?: string | null;
  } = {}): Promise<ConversationListResponse> {
    const podId = this.requirePodId(options.pod_id);
    return this.http.request<ConversationListResponse>("GET", `/pods/${podId}/conversations`, {
      params: {
        agent_name: options.agent_name === null
          ? POD_DEFAULT_AGENT_SELECTOR
          : options.agent_name,
        parent_id: options.parent_id,
        type: options.type,
        limit: options.limit ?? 20,
        page_token: options.page_token,
      },
    }).then(normalizeConversationList);
  }

  listByAgent(
    agentName: string,
    options: {
      pod_id?: string | null;
      limit?: number;
      page_token?: string | null;
    } = {},
  ): Promise<ConversationListResponse> {
    return this.list({ ...options, agent_name: agentName });
  }

  listDefault(
    options: {
      pod_id?: string | null;
      limit?: number;
      page_token?: string | null;
    } = {},
  ): Promise<ConversationListResponse> {
    return this.list({ ...options, agent_name: POD_DEFAULT_AGENT_SELECTOR });
  }

  async listModels(options: { orgId?: string | null } = {}): Promise<{ items: AvailableModelInfo[]; limit: number; next_page_token: null }> {
    const orgId = options.orgId?.trim();
    if (orgId) {
      const catalog = await this.listProfileCatalog(orgId);
      const items = this.modelOptionsFromProfiles(catalog);
      return {
        items,
        limit: items.length,
        next_page_token: null,
      };
    }

    const catalog = await this.listRuntimeCatalog();
    const items = catalog.items.flatMap((harness) =>
      (harness.models ?? []).map((model) => ({
        id: model as ConversationModel,
        name: model,
        harness_kind: harness.harness_kind,
        description: harness.daemon_display_name,
      })),
    );
    return {
      items,
      limit: items.length,
      next_page_token: null,
    };
  }

  async create(payload: ConversationCreateInput = {}): Promise<Conversation> {
    const podId = this.requirePodId(payload.pod_id);
    const { agent_name, harness_kind, model, model_name, pod_id, profile_id, ...requestBody } = payload;
    const agentRuntime = await this.resolveAgentRuntime(
      requestBody.agent_runtime,
      model_name ?? model,
      harness_kind,
      profile_id,
    );
    const body: CreateConversationRequest = {
      ...requestBody,
      agent_name: agent_name ?? undefined,
      agent_runtime: agentRuntime,
    };

    void pod_id;

    return this.http.request<Conversation>("POST", `/pods/${podId}/conversations`, {
      body,
    }).then(normalizeConversation);
  }

  createForAgent(
    agentName: string,
    payload: Omit<ConversationCreateInput, "agent_name"> = {},
  ): Promise<Conversation> {
    return this.create({
      ...payload,
      agent_name: agentName,
    });
  }

  get(conversationId: string, options: { pod_id?: string | null } = {}): Promise<Conversation> {
    const podId = this.requirePodId(options.pod_id);
    return this.http.request<Conversation>("GET", `/pods/${podId}/conversations/${conversationId}`)
      .then(normalizeConversation);
  }

  async update(
    conversationId: string,
    payload: ConversationUpdateInput,
    options: { pod_id?: string | null } = {},
  ): Promise<Conversation> {
    const podId = this.requirePodId(options.pod_id);
    const { harness_kind, model, model_name, profile_id, ...requestBody } = payload;
    const agentRuntime = await this.resolveAgentRuntime(
      requestBody.agent_runtime,
      model_name ?? model,
      harness_kind,
      profile_id,
    );
    const body: UpdateConversationRequest = {
      ...requestBody,
      agent_runtime: agentRuntime,
    };

    return this.http.request<Conversation>("PATCH", `/pods/${podId}/conversations/${conversationId}`, {
      body,
    }).then(normalizeConversation);
  }

  sendMessageStream(
    conversationId: string,
    payload: SendMessageRequest,
    options: { pod_id?: string | null; signal?: AbortSignal } = {},
  ) {
    const podId = this.requirePodId(options.pod_id);
    return this.http.stream(`/pods/${podId}/conversations/${conversationId}/messages`, {
      method: "POST",
      body: payload,
      signal: options.signal,
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
    });
  }

  resumeStream(
    conversationId: string,
    options: { pod_id?: string | null; signal?: AbortSignal } = {},
  ) {
    const podId = this.requirePodId(options.pod_id);
    return this.http.stream(`/pods/${podId}/conversations/${conversationId}/stream`, {
      signal: options.signal,
      headers: {
        Accept: "text/event-stream",
      },
    });
  }

  stopRun(conversationId: string, options: { pod_id?: string | null } = {}): Promise<Conversation> {
    const podId = this.requirePodId(options.pod_id);
    return this.http.request<Conversation>("POST", `/pods/${podId}/conversations/${conversationId}/stop`, {
      body: {},
    }).then(normalizeConversation);
  }

  readonly messages = {
    list: (
      conversationId: string,
      options: {
        limit?: number;
        page_token?: string | null;
        before_sequence?: number | null;
        after_sequence?: number | null;
        pod_id?: string | null;
      } = {},
    ): Promise<CursorPage<ConversationMessage>> => {
      const podId = this.requirePodId(options.pod_id);
      return this.http.request<CursorPage<ConversationMessage>>(
        "GET",
        `/pods/${podId}/conversations/${conversationId}/messages`,
        {
          params: {
            page_token: options.page_token,
            before_sequence: options.before_sequence,
            after_sequence: options.after_sequence,
            limit: options.limit ?? 100,
          },
        },
      ).then((response) => ({
        ...response,
        items: (response.items ?? []).map(normalizeMessage),
      }));
    },

    send: (
      conversationId: string,
      payload: SendMessageRequest,
      options: { pod_id?: string | null } = {},
    ): Promise<unknown> => {
      const podId = this.requirePodId(options.pod_id);
      return this.http.request<unknown>("POST", `/pods/${podId}/conversations/${conversationId}/messages`, {
        body: payload,
      });
    },
  };

  readonly approvals = {
    list: (
      conversationId: string,
      options: { pod_id?: string | null } = {},
    ): Promise<UserApprovalListResponse> => {
      const podId = this.requirePodId(options.pod_id);
      return this.http.request<UserApprovalListResponse>(
        "GET",
        `/pods/${podId}/conversations/${conversationId}/approvals`,
      );
    },

    resolve: (
      conversationId: string,
      approvalId: string,
      payload: ResolveUserApprovalInput,
      options: { pod_id?: string | null } = {},
    ): Promise<ApprovalDecisionResponse> => {
      const podId = this.requirePodId(options.pod_id);
      const body: ResolveUserApprovalRequest = {
        ...payload,
        decision: payload.decision as ResolveUserApprovalRequest["decision"],
      };

      return this.http.request<ApprovalDecisionResponse>(
        "POST",
        `/pods/${podId}/conversations/${conversationId}/approvals/${approvalId}/decision`,
        { body },
      );
    },
  };
}
