import type { GeneratedClientAdapter } from "../generated.js";
import type { OutreachRequest } from "../openapi_client/models/OutreachRequest.js";
import type { WebWidgetCreateRequest } from "../openapi_client/models/WebWidgetCreateRequest.js";
import type { WebWidgetUpdateRequest } from "../openapi_client/models/WebWidgetUpdateRequest.js";
import { AgentSurfacesService } from "../openapi_client/services/AgentSurfacesService.js";
import { ContactOutreachService } from "../openapi_client/services/ContactOutreachService.js";
import { ContactsService } from "../openapi_client/services/ContactsService.js";
import { UsageService } from "../openapi_client/services/UsageService.js";

/**
 * The people a pod's bots answer who are not members.
 *
 * A contact wrote to one of the pod's own bots privately -- from WhatsApp,
 * Telegram or an authenticated email address -- while the bot's
 * `config.contacts.answer` was `known` or `anyone`. They never sign in and hold
 * no grant; they are answered from what the pod made Public. The pod's editors
 * and admins see them (`contact.read`); `remove` takes a pod admin.
 *
 * Contacts are never billed. What answering them may cost an organization a
 * month is its contacts cap: the deployment's default until an organization
 * owner sets one.
 */
export class ContactsNamespace {
  constructor(private readonly client: GeneratedClientAdapter) {}

  /** The pod's contacts, newest first. Pass the opaque `next_cursor` back as
   *  `cursor` for the next page. */
  list(podId: string, options: { limit?: number; cursor?: string } = {}) {
    return this.client.request(() =>
      ContactsService.contactList(podId, options.limit ?? 50, options.cursor),
    );
  }

  /** One contact, with the handles they are known by. */
  get(podId: string, contactId: string) {
    return this.client.request(() => ContactsService.contactGet(podId, contactId));
  }

  /** Change the name a contact is addressed by. */
  rename(podId: string, contactId: string, displayName: string | null) {
    return this.client.request(() =>
      ContactsService.contactUpdate(podId, contactId, { display_name: displayName }),
    );
  }

  /** Write to a contact first, on one of their handles: email any time,
   *  WhatsApp within 24 hours of their last message there, never a handle that
   *  asked the pod to stop. Returns the line, in their channel at that handle's
   *  place (opened if new), and its delivery -- `PENDING` until it goes. */
  outreach(
    podId: string,
    contactId: string,
    payload: OutreachRequest,
    options: { idempotencyKey?: string } = {},
  ) {
    return this.client.request(() =>
      ContactOutreachService.contactOutreachSend(podId, contactId, payload, options.idempotencyKey),
    );
  }

  /** One page of everything the pod holds about a contact: handles,
   *  conversations, then rows. Pass `next_cursor` back as `cursor` until absent. */
  export(podId: string, contactId: string, options: { cursor?: string } = {}) {
    return this.client.request(() =>
      ContactsService.contactExport(podId, contactId, options.cursor),
    );
  }

  /** Forget a contact: their rows, handles, conversations and chat sessions. */
  remove(podId: string, contactId: string) {
    return this.client.request(() => ContactsService.contactDelete(podId, contactId));
  }

  /** The organization's monthly cap on answering contacts, and this month's spend. */
  cap(organizationId: string) {
    return this.client.request(() =>
      UsageService.usageOrganizationContactsCapGet(organizationId),
    );
  }

  /** Set the cap in USD, or `null` for no limit. Takes an organization owner. */
  setCap(organizationId: string, monthlyLimitUsd: number | null) {
    return this.client.request(() =>
      UsageService.usageOrganizationContactsCapUpdate(organizationId, {
        monthly_limit_usd: monthlyLimitUsd,
      }),
    );
  }

  /**
   * Web widgets: the pod's chat on other people's pages, and the key a form page adds rows with. The public key
   * goes in the page and names the widget only; the signing secret, returned by
   * `create` and `reissue` once, stays on the customer's server.
   */
  readonly widgets = {
    list: (podId: string) =>
      this.client.request(() => AgentSurfacesService.agentWebWidgetList(podId)),
    create: (podId: string, payload: WebWidgetCreateRequest) =>
      this.client.request(() => AgentSurfacesService.agentWebWidgetCreate(podId, payload)),
    update: (podId: string, widgetId: string, payload: WebWidgetUpdateRequest) =>
      this.client.request(() =>
        AgentSurfacesService.agentWebWidgetUpdate(podId, widgetId, payload),
      ),
    reissue: (podId: string, widgetId: string) =>
      this.client.request(() =>
        AgentSurfacesService.agentWebWidgetReissue(podId, widgetId),
      ),
    remove: (podId: string, widgetId: string) =>
      this.client.request(() => AgentSurfacesService.agentWebWidgetDelete(podId, widgetId)),
  };
}
