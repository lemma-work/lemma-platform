import type { GeneratedClientAdapter } from "../generated.js";
import type { AgentRuntimeConfig } from "../openapi_client/models/AgentRuntimeConfig.js";
import type { AgentHarnessListResponse } from "../openapi_client/models/AgentHarnessListResponse.js";
import type { AgentRuntimeProfileListResponse } from "../openapi_client/models/AgentRuntimeProfileListResponse.js";
import type { AgentRuntimeProfileResponse } from "../openapi_client/models/AgentRuntimeProfileResponse.js";
import type { CreateAnthropicCompatibleRuntimeProfileRequest } from "../openapi_client/models/CreateAnthropicCompatibleRuntimeProfileRequest.js";
import type { CreateAgentHostRuntimeProfileRequest } from "../openapi_client/models/CreateAgentHostRuntimeProfileRequest.js";
import type { CreateOpenAICompatibleRuntimeProfileRequest } from "../openapi_client/models/CreateOpenAICompatibleRuntimeProfileRequest.js";
import type { CreateUserDaemonRuntimeProfileRequest } from "../openapi_client/models/CreateUserDaemonRuntimeProfileRequest.js";
import { AgentRuntimeService } from "../openapi_client/services/AgentRuntimeService.js";

export type CreateAgentRuntimeProfileRequest =
  | CreateUserDaemonRuntimeProfileRequest
  | CreateOpenAICompatibleRuntimeProfileRequest
  | CreateAnthropicCompatibleRuntimeProfileRequest
  | CreateAgentHostRuntimeProfileRequest;

export type CreateAgentRuntimeRequest = CreateAgentRuntimeProfileRequest;
export type AgentRuntimeListResponse = AgentRuntimeProfileListResponse;
export type AgentRuntimeResponse = AgentRuntimeProfileResponse;

export class AgentRuntimeNamespace {
  constructor(private readonly client: GeneratedClientAdapter) {}

  listHarnesses(): Promise<AgentHarnessListResponse> {
    return this.client.request(() => AgentRuntimeService.agentRuntimeHarnessesList());
  }

  listAvailableHarnesses(): Promise<AgentHarnessListResponse> {
    return this.listHarnesses();
  }

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
