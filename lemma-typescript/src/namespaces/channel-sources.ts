import type { GeneratedClientAdapter } from "../generated.js";
import type { SourceUpdateRequest } from "../openapi_client/models/SourceUpdateRequest.js";
import type { WhatsAppGroupRequest } from "../openapi_client/models/WhatsAppGroupRequest.js";
import { ChannelSourcesService } from "../openapi_client/services/ChannelSourcesService.js";
import { ChannelsService } from "../openapi_client/services/ChannelsService.js";

/**
 * The outside places a pod's group channels are fed by, from the bot's side:
 * `client.channels.sources`.
 *
 * A WhatsApp or Telegram group, a Slack or Teams channel, a group DM: each is a
 * source of a GROUP channel, read and written in through `channels`. Here is
 * only what the bot keeps about it -- whether people outside the pod are
 * answered there, and which member answers for them -- and the two ways to
 * bring the bot into a group: `startWhatsAppGroup` (a business number creates
 * groups, it cannot join one; it comes back `pending` until WhatsApp confirms
 * it) and `telegramLink` (a one-use, hour-long link that adds the bot to a
 * group the caller picks).
 */
export class ChannelSourcesNamespace {
  constructor(private readonly client: GeneratedClientAdapter) {}

  /** The groups one bot is in, as channels, each with its `source_settings`;
   *  most recently active first. Pass `next_cursor` back as `cursor`. */
  list(podId: string, surfaceName: string, options: { cursor?: string; limit?: number } = {}) {
    return this.client.request(() =>
      ChannelsService.channelList(
        podId,
        undefined, // kind
        undefined, // visibility
        undefined, // q
        false,
        undefined,
        undefined,
        false,
        undefined,
        false, // needsPerson
        false, // joined
        surfaceName,
        options.cursor,
        options.limit ?? 50,
      ),
    );
  }

  /** A group source's settings: who answers its outsiders, and from what. */
  get(podId: string, channelId: string, sourceId: string) {
    return this.client.request(() =>
      ChannelSourcesService.channelSourceGet(podId, channelId, sourceId),
    );
  }

  /** Switch outsiders on or off in one group, or take it over
   *  (`owner: "me"`). Absent is unchanged. */
  update(podId: string, channelId: string, sourceId: string, payload: SourceUpdateRequest) {
    return this.client.request(() =>
      ChannelSourcesService.channelSourceUpdate(podId, channelId, sourceId, payload),
    );
  }

  /** Start a WhatsApp group with the pod's bot in it: a new group channel
   *  (`channel_id`). The same `idempotencyKey` answers with the group it started. */
  startWhatsAppGroup(
    podId: string,
    payload: WhatsAppGroupRequest,
    options: { idempotencyKey?: string } = {},
  ) {
    return this.client.request(() =>
      ChannelSourcesService.channelWhatsappGroupStart(podId, payload, options.idempotencyKey),
    );
  }

  /** A one-use, hour-long Telegram link that adds the bot to a group. */
  telegramLink(podId: string, surfaceName: string) {
    return this.client.request(() =>
      ChannelSourcesService.channelTelegramLinkCreate(podId, { surface_name: surfaceName }),
    );
  }
}
