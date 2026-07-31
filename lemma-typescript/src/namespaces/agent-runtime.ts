import type { GeneratedClientAdapter } from "../generated.js";
import type { AgentRuntimeConfig } from "../openapi_client/models/AgentRuntimeConfig.js";
import type { AgentRuntimeProfileListResponse } from "../openapi_client/models/AgentRuntimeProfileListResponse.js";
import type { AgentRuntimeProfileResponse } from "../openapi_client/models/AgentRuntimeProfileResponse.js";
import type { CreateAgentHostRuntimeProfileRequest } from "../openapi_client/models/CreateAgentHostRuntimeProfileRequest.js";
import type { CreateAnthropicCompatibleRuntimeProfileRequest } from "../openapi_client/models/CreateAnthropicCompatibleRuntimeProfileRequest.js";
import type { CreateOpenAICompatibleRuntimeProfileRequest } from "../openapi_client/models/CreateOpenAICompatibleRuntimeProfileRequest.js";
import { AgentRuntimeService } from "../openapi_client/services/AgentRuntimeService.js";

// Mirrors the discriminated union the create endpoint accepts. Keep all three
// members: omitting one makes that profile kind uncreatable through the typed
// client even though the endpoint takes it.
export type CreateAgentRuntimeProfileRequest =
  | CreateAgentHostRuntimeProfileRequest
  | CreateOpenAICompatibleRuntimeProfileRequest
  | CreateAnthropicCompatibleRuntimeProfileRequest;

// Compile-time guard on the union above. It is hand-written while the members
// are generated from the schema, so a profile kind the API accepts can silently
// go missing here and become uncreatable from TypeScript - which is what had
// happened to the Agent Host kind. Each generated model must satisfy the union;
// drop one and `tsc` fails on this declaration. Test files are excluded from the
// tsconfig, so this assertion only bites if it lives in checked source.
type MemberOfCreateUnion<T extends CreateAgentRuntimeProfileRequest> = T;
type _CreateUnionIsExhaustive =
  | MemberOfCreateUnion<CreateAgentHostRuntimeProfileRequest>
  | MemberOfCreateUnion<CreateOpenAICompatibleRuntimeProfileRequest>
  | MemberOfCreateUnion<CreateAnthropicCompatibleRuntimeProfileRequest>;

export type CreateAgentRuntimeRequest = CreateAgentRuntimeProfileRequest;
export type AgentRuntimeListResponse = AgentRuntimeProfileListResponse;
export type AgentRuntimeResponse = AgentRuntimeProfileResponse;

export class AgentRuntimeNamespace {
  constructor(private readonly client: GeneratedClientAdapter) {}

  listRuntimes(orgId: string): Promise<AgentRuntimeListResponse> {
    return this.listProfiles(orgId);
  }

  listProfiles(orgId: string): Promise<AgentRuntimeProfileListResponse> {
    return this.client.request(() => AgentRuntimeService.agentRuntimeProfilesList(orgId));
  }

  createRuntime(
    orgId: string,
    request: CreateAgentRuntimeRequest,
  ): Promise<AgentRuntimeResponse> {
    return this.createProfile(orgId, request);
  }

  createProfile(
    orgId: string,
    request: CreateAgentRuntimeProfileRequest,
  ): Promise<AgentRuntimeProfileResponse> {
    return this.client.request(() => AgentRuntimeService.agentRuntimeProfilesCreate(orgId, request));
  }

  /**
   * @deprecated Runtime defaults are now pod config (`default_profile_id`) or
   * organization Agent Runtimes. The backend no longer exposes a global
   * default-runtime mutation endpoint.
   */
  updateDefault(agentRuntime: AgentRuntimeConfig): Promise<never> {
    void agentRuntime;
    return Promise.reject(new Error(
      "agentRuntime.updateDefault is no longer supported. Update pod config.default_profile_id instead.",
    ));
  }
}
