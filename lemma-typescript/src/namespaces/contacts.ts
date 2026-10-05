import type { GeneratedClientAdapter } from "../generated.js";
import type { WebWidgetCreateRequest } from "../openapi_client/models/WebWidgetCreateRequest.js";
import type { WebWidgetUpdateRequest } from "../openapi_client/models/WebWidgetUpdateRequest.js";
import { AgentSurfacesService } from "../openapi_client/services/AgentSurfacesService.js";
import { ContactsService } from "../openapi_client/services/ContactsService.js";
import { UsageService } from "../openapi_client/services/UsageService.js";

/**
 * The people a pod's bots answer who are not members.
 *
 * A contact wrote to one of the pod's own bots privately -- from WhatsApp,
 * Telegram or an authenticated email address -- while the bot's
 * `config.contacts.answer` was `known` or `anyone`. They never sign in and hold
 * no grant; they are answered from what the pod made Public. Every member who
 * can read the pod can list them; `remove` takes a pod admin.
 *
 * Contacts are never billed. What answering them may cost an organization a
 * month is its contacts cap, set by an organization owner or editor.
 */
export class ContactsNamespace {
  constructor(private readonly client: GeneratedClientAdapter) {}

  /** The pod's contacts, newest first. Page with `next_before`. */
  list(podId: string, options: { limit?: number; before?: string } = {}) {
    return this.client.request(() =>
      ContactsService.contactList(podId, options.limit ?? 50, options.before),
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

  /** Write to a contact in their latest conversation, where the channel allows:
   *  never where they unsubscribed, and on WhatsApp only within 24 hours of
   *  their last message. */
  followUp(podId: string, contactId: string, message: string) {
    return this.client.request(() =>
      ContactsService.contactFollowUp(podId, contactId, { message }),
    );
  }

  /** Everything the pod holds about a contact: handles and conversations. */
  export(podId: string, contactId: string) {
    return this.client.request(() => ContactsService.contactExport(podId, contactId));
  }

  /** Forget a contact, their handles and their conversations. */
  remove(podId: string, contactId: string) {
    return this.client.request(() => ContactsService.contactDelete(podId, contactId));
  }

  /** The organization's monthly cap on answering contacts, and this month's spend. */
  cap(organizationId: string) {
    return this.client.request(() =>
      UsageService.usageOrganizationContactsCapGet(organizationId),
    );
  }

  /** Set the cap in USD, or clear it with `null`. */
  setCap(organizationId: string, monthlyLimitUsd: number | null) {
    return this.client.request(() =>
      UsageService.usageOrganizationContactsCapUpdate(organizationId, {
        monthly_limit_usd: monthlyLimitUsd,
      }),
    );
  }

  /**
   * Web widgets: chat bubbles and forms for other people's pages. The public key
   * goes in the page and names the widget only; the signing secret, returned by
   * `create` and `rotateWidgetSecret` once, stays on the customer's server.
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
    rotateSecret: (podId: string, widgetId: string) =>
      this.client.request(() =>
        AgentSurfacesService.agentWebWidgetRotateSecret(podId, widgetId),
      ),
    remove: (podId: string, widgetId: string) =>
      this.client.request(() => AgentSurfacesService.agentWebWidgetDelete(podId, widgetId)),
  };
}
