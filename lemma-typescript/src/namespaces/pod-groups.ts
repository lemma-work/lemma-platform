import type { GeneratedClientAdapter } from "../generated.js";
import type { GroupStartRequest } from "../openapi_client/models/GroupStartRequest.js";
import type { GroupUpdateRequest } from "../openapi_client/models/GroupUpdateRequest.js";
import { AgentSurfacesService } from "../openapi_client/services/AgentSurfacesService.js";

/**
 * A pod's groups: the WhatsApp, Telegram and Slack chats its bots are in, as
 * one place in the pod. People in the pod are answered there as themselves;
 * people outside it get what the pod made Public, and anything more goes to
 * the member who answers for them.
 *
 * `list` and `get` are readable by every member. `start` creates a WhatsApp
 * group (a business number creates groups, it cannot join one) and comes back
 * `pending` until WhatsApp confirms it, when its `invite_link` appears.
 * `link` returns a one-use Telegram link that adds the bot to a group the
 * caller picks. A question a group's outsiders are `waiting` on is answered
 * through `notifications.respond` with its `notification_id`: the bot relays
 * the answer in the group.
 */
export class PodGroupsNamespace {
  constructor(private readonly client: GeneratedClientAdapter) {}

  /** Every group the pod's bots are in, most recently changed first. */
  list(podId: string) {
    return this.client.request(() => AgentSurfacesService.agentGroupList(podId));
  }

  /** One group, with the people seen in it and what is waiting on the caller. */
  get(podId: string, groupId: string) {
    return this.client.request(() =>
      AgentSurfacesService.agentGroupGet(podId, groupId),
    );
  }

  /** What was said in the group, oldest first (WhatsApp and Telegram groups). */
  timeline(podId: string, groupId: string, options: { limit?: number } = {}) {
    return this.client.request(() =>
      AgentSurfacesService.agentGroupTimeline(podId, groupId, options.limit ?? 60),
    );
  }

  /** Start a WhatsApp group with the pod's bot in it. */
  start(podId: string, payload: GroupStartRequest) {
    return this.client.request(() =>
      AgentSurfacesService.agentGroupStart(podId, payload),
    );
  }

  /** A one-use, hour-long Telegram link that adds the bot to a group. */
  link(podId: string, surfaceName: string) {
    return this.client.request(() =>
      AgentSurfacesService.agentGroupLink(podId, { surface_name: surfaceName }),
    );
  }

  /** Switch outsiders on or off in one group, or take it over. */
  update(podId: string, groupId: string, payload: GroupUpdateRequest) {
    return this.client.request(() =>
      AgentSurfacesService.agentGroupUpdate(podId, groupId, payload),
    );
  }
}
