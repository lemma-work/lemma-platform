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

  /**
   * The organization-level permission ids you hold in it, such as
   * `org.installation.manage`. Ask for a permission, not a role: the server
   * decides who holds what.
   */
  permissionsMe(orgId: string) {
    return this.http.request<{ organization_id: string; actions: string[] }>(
      "GET",
      `/organizations/${encodeURIComponent(orgId)}/permissions/me`,
    );
  }

  /** One organization's pods, with their apps, agents and your roles. */
  home(orgId: string) {
    return this.client.request(() => OrganizationsService.orgHome(orgId));
  }

  /**
   * Everything waiting on you in every pod you may open in one organization,
   * oldest first: questions, approvals and permission requests in your
   * conversations, workflow forms and decisions assigned to you, and contacts
   * waiting on a person where you look after contacts. One request instead of
   * one per pod per kind.
   */
  needsYou(orgId: string, options: { limit?: number } = {}) {
    return this.client.request(() =>
      OrganizationsService.orgNeedsYouList(orgId, options.limit ?? 100),
    );
  }

  /**
   * What moved lately in each pod you may open in one organization: your
   * latest chats, the workflows and schedules that ran, and your conversations
   * per day, split by whether an agent or a person started them. Pass your
   * IANA `timezone` so the days are your days.
   */
  recents(orgId: string, options: { days?: number; chats?: number; timezone?: string } = {}) {
    return this.client.request(() =>
      OrganizationsService.orgRecentsGet(
        orgId,
        options.days ?? 14,
        options.chats ?? 10,
        options.timezone ?? "UTC",
      ),
    );
  }

  /** Every app you may open across one organization's pods, newest first. */
  apps(orgId: string) {
    return this.client.request(() => OrganizationsService.orgAppsList(orgId));
  }

  /** When you last looked at an organization's Home, kept for you by the server. */
  readonly homeSeen = {
    get: (orgId: string) =>
      this.client.request(() => OrganizationsService.orgHomeSeenGet(orgId)),

    /**
     * Mark Home seen as of `seenAt` (an ISO time with its offset -- typically
     * when the page loaded), or now. The mark never moves back.
     */
    mark: (orgId: string, seenAt?: string) =>
      this.client.request(() =>
        OrganizationsService.orgHomeSeenSet(orgId, seenAt ? { seen_at: seenAt } : null),
      ),
  };

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
