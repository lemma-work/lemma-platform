import type { GeneratedClientAdapter } from "../generated.js";
import type { UserProfileRequest } from "../openapi_client/models/UserProfileRequest.js";
import type { FirstWorkspaceRequest } from "../openapi_client/models/FirstWorkspaceRequest.js";
import { UsersService } from "../openapi_client/services/UsersService.js";

export class UsersNamespace {
  constructor(private readonly client: GeneratedClientAdapter) {}

  current() {
    return this.client.request(() => UsersService.userCurrentGet());
  }

  ensureFirstWorkspace(payload: FirstWorkspaceRequest = {}) {
    return this.client.request(() => UsersService.usersEnsureFirstWorkspace(payload));
  }

  /** Whether this local installation can send email. 404 on a server. */
  emailDelivery() {
    return this.client.request(() => UsersService.userEmailDeliveryGet());
  }

  /** Send a test email to the signed-in user's own address. Local installations
   *  only; answers `{ ok, message }`, where `message` is fit to show. */
  sendTestEmail() {
    return this.client.request(() => UsersService.userEmailDeliveryTest());
  }

  getProfile() {
    return this.client.request(() => UsersService.userProfileGet());
  }

  upsertProfile(payload: UserProfileRequest) {
    return this.client.request(() => UsersService.userProfileUpsert(payload));
  }
}
