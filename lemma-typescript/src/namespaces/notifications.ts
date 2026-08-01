import type { GeneratedClientAdapter } from "../generated.js";
import type { NotifyMemberRequest } from "../openapi_client/models/NotifyMemberRequest.js";
import { NotificationsService } from "../openapi_client/services/NotificationsService.js";

/**
 * The caller's own inbox, across every pod they belong to.
 *
 * The in-app inbox is the one channel that cannot fail — every `notify` writes
 * here whether or not a chat platform also took the message — so this is the
 * complete record of what a person has been told.
 */
export class NotificationsNamespace {
  constructor(private readonly client: GeneratedClientAdapter) {}

  /** My notifications, newest first. */
  list(params: {
    podId?: string;
    unreadOnly?: boolean;
    limit?: number;
    before?: string;
  } = {}) {
    return this.client.request(() =>
      NotificationsService.notificationList(
        params.podId,
        params.unreadOnly,
        params.limit,
        params.before,
      ),
    );
  }

  /** Just the badge number. */
  unreadCount(podId?: string) {
    return this.client.request(() =>
      NotificationsService.notificationUnreadCount(podId),
    );
  }

  markRead(notificationId: string) {
    return this.client.request(() =>
      NotificationsService.notificationMarkRead(notificationId),
    );
  }

  markAllRead(podId?: string) {
    return this.client.request(() =>
      NotificationsService.notificationMarkAllRead(podId),
    );
  }
}

/**
 * Reaching a pod member.
 *
 * Prefer this over `podSurfaces.send`: it picks whichever channel the person
 * last used and always leaves the message in their Lemma inbox, so it cannot
 * silently reach nobody.
 */
export class PodNotifyNamespace {
  constructor(
    private readonly client: GeneratedClientAdapter,
    private readonly podId: () => string,
  ) {}

  notify(payload: NotifyMemberRequest) {
    return this.client.request(() =>
      NotificationsService.podNotify(this.podId(), payload),
    );
  }
}
