import type { GeneratedClientAdapter } from "../generated.js";
import type { AgentRuntimeConfig } from "../openapi_client/models/AgentRuntimeConfig.js";
import type { AgentRuntimeProfileDetailResponse } from "../openapi_client/models/AgentRuntimeProfileDetailResponse.js";
import type { AgentRuntimeProfileListResponse } from "../openapi_client/models/AgentRuntimeProfileListResponse.js";
import type { AgentRuntimeProfileResponse } from "../openapi_client/models/AgentRuntimeProfileResponse.js";
import type { CreateAgentHostRuntimeProfileRequest } from "../openapi_client/models/CreateAgentHostRuntimeProfileRequest.js";
import type { CreateAnthropicCompatibleRuntimeProfileRequest } from "../openapi_client/models/CreateAnthropicCompatibleRuntimeProfileRequest.js";
import type { CreateOpenAICompatibleRuntimeProfileRequest } from "../openapi_client/models/CreateOpenAICompatibleRuntimeProfileRequest.js";
import type { UpdateAgentHostRuntimeProfileRequest } from "../openapi_client/models/UpdateAgentHostRuntimeProfileRequest.js";
import type { UpdateAnthropicCompatibleRuntimeProfileRequest } from "../openapi_client/models/UpdateAnthropicCompatibleRuntimeProfileRequest.js";
import type { UpdateOpenAICompatibleRuntimeProfileRequest } from "../openapi_client/models/UpdateOpenAICompatibleRuntimeProfileRequest.js";
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

// Mirrors the discriminated union the update endpoint accepts. Every field is
// optional there: the backend distinguishes "absent" from "explicitly null" via
// the set of keys in the request body, so omitting a key keeps the stored value
// and sending `null` clears it. Notably, omitting `api_key` keeps the stored
// credential - sending it back unchanged is never necessary.
export type UpdateAgentRuntimeProfileRequest =
  | UpdateAgentHostRuntimeProfileRequest
  | UpdateOpenAICompatibleRuntimeProfileRequest
  | UpdateAnthropicCompatibleRuntimeProfileRequest;

// Same guard as the create union above, for the same reason.
type MemberOfUpdateUnion<T extends UpdateAgentRuntimeProfileRequest> = T;
type _UpdateUnionIsExhaustive =
  | MemberOfUpdateUnion<UpdateAgentHostRuntimeProfileRequest>
  | MemberOfUpdateUnion<UpdateOpenAICompatibleRuntimeProfileRequest>
  | MemberOfUpdateUnion<UpdateAnthropicCompatibleRuntimeProfileRequest>;

export type CreateAgentRuntimeRequest = CreateAgentRuntimeProfileRequest;
export type UpdateAgentRuntimeRequest = UpdateAgentRuntimeProfileRequest;
export type AgentRuntimeListResponse = AgentRuntimeProfileListResponse;
export type AgentRuntimeResponse = AgentRuntimeProfileResponse;
export type AgentRuntimeDetailResponse = AgentRuntimeProfileDetailResponse;

export class AgentRuntimeNamespace {
  constructor(private readonly client: GeneratedClientAdapter) {}

  listRuntimes(orgId: string): Promise<AgentRuntimeListResponse> {
    return this.listProfiles(orgId);
  }

  listProfiles(
    orgId: string,
    options: { includeDisabled?: boolean } = {},
  ): Promise<AgentRuntimeProfileListResponse> {
    return this.client.request(() =>
      AgentRuntimeService.agentRuntimeProfilesList(orgId, options.includeDisabled ?? false),
    );
  }

  /** Read one profile, including the live harness and host status behind it. */
  getProfile(orgId: string, profileId: string): Promise<AgentRuntimeProfileDetailResponse> {
    return this.client.request(() =>
      AgentRuntimeService.agentRuntimeProfilesGet(orgId, profileId),
    );
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
   * Patch a profile. Send only the fields that changed - a key left out keeps
   * its stored value, which is how a rename avoids resending the API key.
   */
  updateProfile(
    orgId: string,
    profileId: string,
    request: UpdateAgentRuntimeProfileRequest,
  ): Promise<AgentRuntimeProfileResponse> {
    return this.client.request(() =>
      AgentRuntimeService.agentRuntimeProfilesUpdate(orgId, profileId, request),
    );
  }

  /**
   * Archive a profile: it stops appearing in the catalog and cannot be selected
   * for new runs, but is retained and can be restored. There is no hard delete.
   */
  archiveProfile(orgId: string, profileId: string): Promise<void> {
    return this.client.request(() =>
      AgentRuntimeService.agentRuntimeProfilesArchive(orgId, profileId),
    );
  }

  /** Bring an archived profile back into the catalog. */
  restoreProfile(orgId: string, profileId: string): Promise<AgentRuntimeProfileResponse> {
    return this.client.request(() =>
      AgentRuntimeService.agentRuntimeProfilesRestore(orgId, profileId),
    );
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
