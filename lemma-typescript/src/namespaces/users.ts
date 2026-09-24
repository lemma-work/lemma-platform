import type { GeneratedClientAdapter } from "../generated.js";
import type { UserProfileRequest } from "../openapi_client/models/UserProfileRequest.js";
import type { FirstWorkspaceRequest } from "../openapi_client/models/FirstWorkspaceRequest.js";
import { UsersService } from "../openapi_client/services/UsersService.js";

export class UsersNamespace {
  constructor(private readonly client: GeneratedClientAdapter) {}

  current() {
    return this.client.request(() => UsersService.userCurrentGet());
  }

  /** What this installation is, whether the caller owns it, and who may sign up. */
  installation() {
    return this.client.request(() => UsersService.userInstallationGet());
  }

  ensureFirstWorkspace(payload: FirstWorkspaceRequest = {}) {
    return this.client.request(() => UsersService.usersEnsureFirstWorkspace(payload));
  }

  getProfile() {
    return this.client.request(() => UsersService.userProfileGet());
  }

  upsertProfile(payload: UserProfileRequest) {
    return this.client.request(() => UsersService.userProfileUpsert(payload));
  }
}
