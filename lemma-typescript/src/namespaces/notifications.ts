import type { GeneratedClientAdapter } from "../generated.js";
import type { NotificationRespondRequest } from "../openapi_client/models/NotificationRespondRequest.js";
import type { NotificationStatus } from "../openapi_client/models/NotificationStatus.js";
import type { NotifyMemberRequest } from "../openapi_client/models/NotifyMemberRequest.js";
import { NotificationsService } from "../openapi_client/services/NotificationsService.js";

/**
 * Things the pod has asked the current user for, and the ways to answer them.
 *
 * Every method except {@link send} is scoped to the caller's own notifications —
 * there is no way to read somebody else's, by design.
 *
 * Two states, read independently. `status` is about the person (`OPEN`,
 * `RESPONDED`, `ACKNOWLEDGED`, `EXPIRED`, `CANCELLED`); `delivery_status` is
 * about the channel (`DELIVERED`, `UNDELIVERABLE`, `FAILED`). `UNDELIVERABLE`
 * is not an error — no chat app or mailbox could carry it, and it is in the
 * inbox regardless.
 */
export class NotificationsNamespace {
  constructor(
    private readonly client: GeneratedClientAdapter,
    private readonly podId: () => string,
  ) {}

  /** My notifications in this pod, newest first. */
  list(options?: {
    status?: Array<NotificationStatus>;
    limit?: number;
    pageToken?: string;
  }) {
    return this.client.request(() =>
      NotificationsService.notificationList(
        this.podId(),
        options?.status,
        options?.limit,
        options?.pageToken,
      ),
    );
  }

  /**
   * How many I have not read. Keyed on being read, not answered — a badge that
   * only clears when you finish the work is a badge people stop looking at.
   */
  unreadCount() {
    return this.client.request(() =>
      NotificationsService.notificationUnreadCount(this.podId()),
    );
  }

  markRead(notificationId: string) {
    return this.client.request(() =>
      NotificationsService.notificationMarkRead(this.podId(), notificationId),
    );
  }

  markAllRead() {
    return this.client.request(() =>
      NotificationsService.notificationMarkAllRead(this.podId()),
    );
  }

  /**
   * Answer one. Produces the same `RESPONDED` an agent-mediated reply on a chat
   * surface produces, so the run that asked reads one thing either way.
   *
   * Rejects with 409 when the notification is answered by completing its
   * `action` instead (a workflow form, submitted through the workflow run
   * endpoint where it is validated against the node's schema), and when
   * somebody has already answered it.
   */
  respond(notificationId: string, payload: NotificationRespondRequest) {
    return this.client.request(() =>
      NotificationsService.notificationRespond(
        this.podId(),
        notificationId,
        payload,
      ),
    );
  }

  /** Dismiss one that asked for nothing. 409 when a response is owed. */
  acknowledge(notificationId: string) {
    return this.client.request(() =>
      NotificationsService.notificationAcknowledge(this.podId(), notificationId),
    );
  }

  /**
   * Reach a pod member wherever they are, leaving a copy in their inbox either
   * way. A 201 whose `delivery_status` is `UNDELIVERABLE` succeeded; read
   * `undeliverable_reason` to tell the user what to do about it.
   */
  send(payload: NotifyMemberRequest) {
    return this.client.request(() =>
      NotificationsService.notificationSend(this.podId(), payload),
    );
  }
}
