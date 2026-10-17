import type { GeneratedClientAdapter } from "../generated.js";
import type { ChannelKind } from "../openapi_client/models/ChannelKind.js";
import { ChannelsService } from "../openapi_client/services/ChannelsService.js";

/**
 * The places a pod's members, contacts and agents talk.
 *
 * A channel is one shared timeline fed by any number of outside sources -- a
 * WhatsApp chat, a Telegram group, a Slack channel, an email thread, a web
 * chat. `DIRECT` is one member and the pod, `CONTACT` one contact and the
 * pod, `GROUP` a chat several people read. Another member's direct channel is
 * never listed.
 */
export class ChannelsNamespace {
  constructor(private readonly client: GeneratedClientAdapter) {}

  /** The pod's channels, most recently active first. Pass the opaque
   *  `next_before` back as `before` for the next page. */
  list(
    podId: string,
    options: { kind?: ChannelKind | `${ChannelKind}`; limit?: number; before?: string } = {},
  ) {
    return this.client.request(() =>
      ChannelsService.channelList(
        podId,
        options.kind as ChannelKind | undefined,
        options.limit ?? 50,
        options.before,
      ),
    );
  }

  /** One channel, with its sources and participants. */
  get(podId: string, channelId: string) {
    return this.client.request(() => ChannelsService.channelGet(podId, channelId));
  }

  /** The channel's timeline, oldest first: who said what, when and where. The
   *  newest page unless `before` is the previous page's `next_before`. */
  messages(podId: string, channelId: string, options: { limit?: number; before?: string } = {}) {
    return this.client.request(() =>
      ChannelsService.channelMessageList(podId, channelId, options.limit ?? 50, options.before),
    );
  }

  /** Note that the caller has read the channel up to now. */
  markRead(podId: string, channelId: string) {
    return this.client.request(() => ChannelsService.channelMarkRead(podId, channelId));
  }

  /** Answer a contact's channel by hand: its agents stop answering there.
   *  Takes `contact.message`. */
  take(podId: string, channelId: string) {
    return this.client.request(() => ChannelsService.channelHandlerTake(podId, channelId));
  }

  /** Hand a contact's channel back to its agents. */
  release(podId: string, channelId: string) {
    return this.client.request(() => ChannelsService.channelHandlerRelease(podId, channelId));
  }
}
