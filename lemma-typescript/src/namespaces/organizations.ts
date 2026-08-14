import type { GeneratedClientAdapter } from "../generated.js";
import type { HttpClient } from "../http.js";
import type { OrganizationCreateRequest } from "../openapi_client/models/OrganizationCreateRequest.js";
import type { OrganizationInvitationListResponse } from "../openapi_client/models/OrganizationInvitationListResponse.js";
import type { OrganizationInvitationRequest } from "../openapi_client/models/OrganizationInvitationRequest.js";
import type { OrganizationInvitationStatus } from "../openapi_client/models/OrganizationInvitationStatus.js";
import type { OrganizationRole } from "../openapi_client/models/OrganizationRole.js";
import { OrganizationsService } from "../openapi_client/services/OrganizationsService.js";

export class OrganizationsNamespace {
  constructor(
    private readonly client: GeneratedClientAdapter,
    private readonly http: HttpClient,
  ) {}

  list(options: { limit?: number; pageToken?: string; cursor?: string } = {}) {
    return this.client.request(() =>
      OrganizationsService.orgList(options.limit ?? 100, options.pageToken ?? options.cursor),
    );
  }

  get(orgId: string) {
    return this.client.request(() => OrganizationsService.orgGet(orgId));
  }

  /**
   * Every organization you belong to, each with the pods you can see in it.
   *
   * One request instead of listing organizations and then a pod list per
   * organization. Names and ids only — use {@link home} for one organization's
   * apps, agents and roles.
   */
  navigation() {
    return this.client.request(() => OrganizationsService.orgNavigation());
  }

  /** One organization's pods, with their apps, agents and your roles. */
  home(orgId: string) {
    return this.client.request(() => OrganizationsService.orgHome(orgId));
  }

  create(payload: OrganizationCreateRequest) {
    return this.client.request(() => OrganizationsService.orgCreate(payload));
  }

  readonly members = {
    list: (
      orgId: string,
      options: { limit?: number; pageToken?: string; cursor?: string } = {},
    ) =>
      this.client.request(() =>
        OrganizationsService.orgMemberList(orgId, options.limit ?? 100, options.pageToken ?? options.cursor),
      ),

    updateRole: (orgId: string, memberId: string, role: OrganizationRole) =>
      this.client.request(() =>
        OrganizationsService.orgMemberUpdateRole(orgId, memberId, { role }),
      ),

    remove: (orgId: string, memberId: string) =>
      this.client.request(() => OrganizationsService.orgMemberRemove(orgId, memberId)),
  };

  readonly invitations = {
    listMine: async (
      options: {
        status?: OrganizationInvitationStatus;
        limit?: number;
        pageToken?: string;
        cursor?: string;
      } = {},
    ) => {
      if (options.status) {
        return this.client.request(() =>
          OrganizationsService.orgInvitationListMine(
            options.status,
            options.limit ?? 100,
            options.pageToken ?? options.cursor,
          ),
        );
      }

      return this.http.request<OrganizationInvitationListResponse>("GET", "/organizations/invitations", {
        params: {
          limit: options.limit ?? 100,
          page_token: options.pageToken ?? options.cursor,
        },
      });
    },

    list: async (
      orgId: string,
      options: {
        status?: OrganizationInvitationStatus;
        limit?: number;
        pageToken?: string;
        cursor?: string;
      } = {},
    ) => {
      if (options.status) {
        return this.client.request(() =>
          OrganizationsService.orgInvitationList(
            orgId,
            options.status,
            options.limit ?? 100,
            options.pageToken ?? options.cursor,
          ),
        );
      }

      return this.http.request<OrganizationInvitationListResponse>(
        "GET",
        `/organizations/${encodeURIComponent(orgId)}/invitations`,
        {
          params: {
            limit: options.limit ?? 100,
            page_token: options.pageToken ?? options.cursor,
          },
        },
      );
    },

    get: (invitationId: string) =>
      this.client.request(() => OrganizationsService.orgInvitationGet(invitationId)),

    invite: (orgId: string, payload: OrganizationInvitationRequest) =>
      this.client.request(() => OrganizationsService.orgInvitationInvite(orgId, payload)),

    accept: (invitationId: string) =>
      this.client.request(() => OrganizationsService.orgInvitationAccept(invitationId)),

    revoke: (invitationId: string) =>
      this.client.request(() => OrganizationsService.orgInvitationRevoke(invitationId)),
  };
}
