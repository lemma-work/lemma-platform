import type { GeneratedClientAdapter } from "../generated.js";
import type { AgentRuntimeConfig } from "../openapi_client/models/AgentRuntimeConfig.js";
import type { AgentRuntimeProfileListResponse } from "../openapi_client/models/AgentRuntimeProfileListResponse.js";
import type { AnthropicCompatibleRuntimeProfileResponse } from "../openapi_client/models/AnthropicCompatibleRuntimeProfileResponse.js";
import type { AzureOpenAIRuntimeProfileResponse } from "../openapi_client/models/AzureOpenAIRuntimeProfileResponse.js";
import type { CreateAnthropicCompatibleRuntimeProfileRequest } from "../openapi_client/models/CreateAnthropicCompatibleRuntimeProfileRequest.js";
import type { CreateAzureOpenAIRuntimeProfileRequest } from "../openapi_client/models/CreateAzureOpenAIRuntimeProfileRequest.js";
import type { CreateGoogleVertexRuntimeProfileRequest } from "../openapi_client/models/CreateGoogleVertexRuntimeProfileRequest.js";
import type { CreateHarnessRuntimeProfileRequest } from "../openapi_client/models/CreateHarnessRuntimeProfileRequest.js";
import type { CreateOpenAICompatibleRuntimeProfileRequest } from "../openapi_client/models/CreateOpenAICompatibleRuntimeProfileRequest.js";
import type { GoogleVertexRuntimeProfileResponse } from "../openapi_client/models/GoogleVertexRuntimeProfileResponse.js";
import type { HarnessRuntimeProfileResponse } from "../openapi_client/models/HarnessRuntimeProfileResponse.js";
import type { OpenAICompatibleRuntimeProfileResponse } from "../openapi_client/models/OpenAICompatibleRuntimeProfileResponse.js";
import type { UpdateRuntimeProfileRequest } from "../openapi_client/models/UpdateRuntimeProfileRequest.js";
import { RuntimeService } from "../openapi_client/services/RuntimeService.js";

export type CreateAgentRuntimeProfileRequest =
  | CreateOpenAICompatibleRuntimeProfileRequest
  | CreateAnthropicCompatibleRuntimeProfileRequest
  | CreateAzureOpenAIRuntimeProfileRequest
  | CreateGoogleVertexRuntimeProfileRequest
  | CreateHarnessRuntimeProfileRequest;

export type AgentRuntimeResponse =
  | OpenAICompatibleRuntimeProfileResponse
  | AnthropicCompatibleRuntimeProfileResponse
  | AzureOpenAIRuntimeProfileResponse
  | GoogleVertexRuntimeProfileResponse
  | HarnessRuntimeProfileResponse;
export type AgentRuntimeListResponse = AgentRuntimeProfileListResponse;

export class AgentRuntimeNamespace {
  constructor(private readonly client: GeneratedClientAdapter) {}

  listRuntimes(orgId: string): Promise<AgentRuntimeListResponse> {
    return this.listProfiles(orgId);
  }

  listProfiles(orgId: string): Promise<AgentRuntimeProfileListResponse> {
    return this.client.request(() => RuntimeService.runtimeProfilesList(orgId));
  }

  getProfile(orgId: string, profileId: string): Promise<AgentRuntimeResponse> {
    return this.client.request(() => RuntimeService.runtimeProfilesGet(orgId, profileId));
  }

  createRuntime(
    orgId: string,
    request: CreateAgentRuntimeProfileRequest,
  ): Promise<AgentRuntimeResponse> {
    return this.createProfile(orgId, request);
  }

  createProfile(
    orgId: string,
    request: CreateAgentRuntimeProfileRequest,
  ): Promise<AgentRuntimeResponse> {
    return this.client.request(() => RuntimeService.runtimeProfilesCreate(orgId, request));
  }

  updateProfile(
    orgId: string,
    profileId: string,
    request: UpdateRuntimeProfileRequest,
  ): Promise<AgentRuntimeResponse> {
    return this.client.request(
      () => RuntimeService.runtimeProfilesUpdate(orgId, profileId, request),
    );
  }

  refreshProfile(orgId: string, profileId: string): Promise<AgentRuntimeResponse> {
    return this.client.request(() => RuntimeService.runtimeProfilesRefresh(orgId, profileId));
  }

  deleteProfile(orgId: string, profileId: string): Promise<void> {
    return this.client.request(() => RuntimeService.runtimeProfilesDelete(orgId, profileId));
  }

  /**
   * @deprecated Runtime defaults are pod configuration. Profiles only define
   * reusable execution settings.
   */
  updateDefault(agentRuntime: AgentRuntimeConfig): Promise<never> {
    void agentRuntime;
    return Promise.reject(new Error(
      "agentRuntime.updateDefault is no longer supported. Update pod config.default_profile_id instead.",
    ));
  }
}
