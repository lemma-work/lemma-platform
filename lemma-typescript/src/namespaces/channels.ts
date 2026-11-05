import type { GeneratedClientAdapter } from "../generated.js";
import type { HttpClient } from "../http.js";
import type { AgentAnswers } from "../openapi_client/models/AgentAnswers.js";
import type { AgentSeat } from "../openapi_client/models/AgentSeat.js";
import type { ChannelCreateRequest } from "../openapi_client/models/ChannelCreateRequest.js";
import type { ChannelEditRequest } from "../openapi_client/models/ChannelEditRequest.js";
import type { ChannelKind } from "../openapi_client/models/ChannelKind.js";
import type { ChannelMessageResponse } from "../openapi_client/models/ChannelMessageResponse.js";
import type { ChannelPostRequest } from "../openapi_client/models/ChannelPostRequest.js";
import type { ChannelUpdateRequest } from "../openapi_client/models/ChannelUpdateRequest.js";
import type { ContactStatus } from "../openapi_client/models/ContactStatus.js";
import type { MemberUpdateRequest } from "../openapi_client/models/MemberUpdateRequest.js";
import type { MyMembershipUpdate } from "../openapi_client/models/MyMembershipUpdate.js";
import type { SenderKind } from "../openapi_client/models/SenderKind.js";
import type { Visibility } from "../openapi_client/models/Visibility.js";
import { AgentSurfacesService } from "../openapi_client/services/AgentSurfacesService.js";
import { ChannelsService } from "../openapi_client/services/ChannelsService.js";
import { ChannelSourcesNamespace } from "./channel-sources.js";

/** A change to one of the caller's own lines: absent is unchanged, `parts:
 *  null` clears them. */
export type ChannelLineEdit = {
  text?: string | null;
  parts?: ChannelPostRequest["parts"] | null;
};

/** An agent seated in a group: by id (answers when named), or with when it answers. */
export type ChannelAgentSeat = string | { id: string; answers?: AgentAnswers | `${AgentAnswers}` };

function seats(agents: ChannelAgentSeat[] | undefined): AgentSeat[] {
  return (agents ?? []).map((agent) =>
    typeof agent === "string"
      ? { id: agent }
      : { id: agent.id, ...(agent.answers ? { answers: agent.answers as AgentAnswers } : {}) },
  );
}

/**
 * The places a pod's members, contacts and agents talk.
 *
 * A channel is one shared timeline fed by any number of outside sources -- a
 * WhatsApp chat, a Telegram group, a Slack channel, an email thread, a web
 * chat -- or by none: a channel made in Lemma. `DIRECT` is one member and the
 * pod, `CONTACT` one contact and the pod (a support inbox), `GROUP` a
 * conversation of several, `public` (every member sees it) or `private` (its
 * members only).
 *
 * Members are addressed as `(kind, id)`: `MEMBER` and a user id, `CONTACT`
 * and a contact id, `AGENT` and an agent id. Updates send only the fields
 * present; `null` clears one.
 */
export class ChannelsNamespace {
  /** A group channel's sources from the bot's side, and bringing the bot into
   *  a group (a WhatsApp group, a Telegram link). */
  readonly sources: ChannelSourcesNamespace;

  constructor(
    private readonly client: GeneratedClientAdapter,
    private readonly http: HttpClient,
  ) {
    this.sources = new ChannelSourcesNamespace(client);
  }

  // -- channels ---------------------------------------------------------------

  /** The channels the caller may see, most recently active first, each with
   *  what is new in it for them and its last line. `assignee` is `me` or a
   *  member's user id; `sourceSurface` (a bot's surface name) keeps the groups
   *  that bot is in, each with its `source_settings`. Pass `next_cursor` back
   *  as `cursor` for the next page. */
  list(
    podId: string,
    options: {
      kind?: ChannelKind | `${ChannelKind}`;
      /** Only public, or only private, channels (a member's and a contact's
       *  own are private). */
      visibility?: Visibility | `${Visibility}`;
      /** Only channels whose title holds these words, in any case. */
      q?: string;
      archived?: boolean;
      /** Only this contact's channel. */
      contactId?: string;
      assignee?: string;
      unassigned?: boolean;
      status?: ContactStatus | `${ContactStatus}`;
      /** Only contacts' channels waiting on a person: open, unassigned, and
       *  the agent off or asking for one. */
      needsPerson?: boolean;
      /** Only channels the caller is in. */
      joined?: boolean;
      sourceSurface?: string;
      cursor?: string;
      limit?: number;
    } = {},
  ) {
    return this.client.request(() =>
      ChannelsService.channelList(
        podId,
        options.kind as ChannelKind | undefined,
        options.visibility as Visibility | undefined,
        options.q,
        options.archived ?? false,
        options.contactId,
        options.assignee,
        options.unassigned ?? false,
        options.status as ContactStatus | undefined,
        options.needsPerson ?? false,
        options.joined ?? false,
        options.sourceSurface,
        options.cursor,
        options.limit ?? 50,
      ),
    );
  }

  /** Make a group in Lemma, with the caller as its owner and the members (user
   *  ids) and agents named in it. The same `idempotencyKey` returns the group
   *  it made. */
  create(
    podId: string,
    body: Omit<ChannelCreateRequest, "agents" | "visibility"> & {
      visibility?: Visibility | `${Visibility}`;
      agents?: ChannelAgentSeat[];
    },
    options: { idempotencyKey?: string } = {},
  ) {
    return this.client.request(() =>
      ChannelsService.channelCreate(
        podId,
        {
          ...body,
          members: body.members ?? [],
          agents: seats(body.agents),
          visibility: body.visibility as Visibility | undefined,
        },
        options.idempotencyKey,
      ),
    );
  }

  /** One channel, with the places it is fed from, its members and agents, and
   *  how the caller hears of it. */
  get(podId: string, channelId: string) {
    return this.client.request(() => ChannelsService.channelGet(podId, channelId));
  }

  /** The caller's own channel with the pod; not found until it is made. */
  mine(podId: string) {
    return this.client.request(() => ChannelsService.channelMeGet(podId));
  }

  /** The caller's own channel with the pod, made if it is not there yet. */
  openMine(podId: string) {
    return this.client.request(() => ChannelsService.channelMeCreate(podId));
  }

  /** Change exactly the fields present; `null` clears. A group: `title`,
   *  `topic`, `visibility`, `archived`, `answers_shared` (on, the default:
   *  an agent's answer made with the asker's own access is read by everybody
   *  in it; off, by the asker alone until they share it). A contact's
   *  channel: `agents_answer`,
   *  `hand_back_after_hours`, `status`, `assignee` (`me` or a user id). */
  update(podId: string, channelId: string, body: ChannelUpdateRequest) {
    return this.client.request(() => ChannelsService.channelUpdate(podId, channelId, body));
  }

  /** Whether a contact's agent answers them. Off, only members do; on hands
   *  them back to the agent at once (nobody is assigned). */
  setAgentAnswers(
    podId: string,
    channelId: string,
    on: boolean,
    options: { handBackAfterHours?: number | null } = {},
  ) {
    return this.update(podId, channelId, {
      agents_answer: on,
      ...(options.handBackAfterHours !== undefined
        ? { hand_back_after_hours: options.handBackAfterHours }
        : {}),
    });
  }

  /** Delete a group made in Lemma and its timeline. A channel an outside place
   *  feeds is archived instead. */
  delete(podId: string, channelId: string) {
    return this.client.request(() => ChannelsService.channelDelete(podId, channelId));
  }

  // -- members ----------------------------------------------------------------

  /** Add members of the pod (user ids) and the pod's agents to a group. */
  addMembers(
    podId: string,
    channelId: string,
    body: { people?: string[]; agents?: ChannelAgentSeat[] },
  ) {
    return this.client.request(() =>
      ChannelsService.channelMemberAdd(podId, channelId, {
        people: body.people ?? [],
        agents: seats(body.agents),
      }),
    );
  }

  /** A person's role in a group, or when an agent in it answers. */
  updateMember(
    podId: string,
    channelId: string,
    kind: SenderKind | `${SenderKind}`,
    memberId: string,
    body: MemberUpdateRequest,
  ) {
    return this.client.request(() =>
      ChannelsService.channelMemberUpdate(podId, channelId, kind as SenderKind, memberId, body),
    );
  }

  /** Take somebody out of a group -- its owners' or an admin's. */
  removeMember(
    podId: string,
    channelId: string,
    kind: SenderKind | `${SenderKind}`,
    memberId: string,
  ) {
    return this.client.request(() =>
      ChannelsService.channelMemberRemove(podId, channelId, kind as SenderKind, memberId),
    );
  }

  /** Leave a channel the caller is in. */
  leave(podId: string, channelId: string) {
    return this.client.request(() => ChannelsService.channelMemberLeave(podId, channelId));
  }

  /** Join a channel the caller may see: a public group, or -- holding
   *  `contact.read` -- a contact's channel. */
  join(podId: string, channelId: string) {
    return this.client.request(() => ChannelsService.channelMemberJoin(podId, channelId));
  }

  /** How the caller hears of this channel's lines outside the app: `notify`,
   *  `notify_via` (`null`: the pod's rule), `muted_until` (`null` unmutes). */
  updateMyMembership(podId: string, channelId: string, body: MyMembershipUpdate) {
    return this.client.request(() =>
      ChannelsService.channelMemberMeUpdate(podId, channelId, body),
    );
  }

  /** The caller has seen the channel's timeline up to line `seq` -- or, with
   *  `threadRootId`, that thread: a mention is read where it was said. It never
   *  goes back, and never past the newest line. */
  markRead(podId: string, channelId: string, seq: number, threadRootId?: string | null) {
    const body = { seq, ...(threadRootId ? { thread_root_id: threadRootId } : {}) };
    return this.client.request(() => ChannelsService.channelRead(podId, channelId, body));
  }

  // -- lines ------------------------------------------------------------------

  /** The timeline, oldest first: the newest page, the page before `beforeSeq`,
   *  or the page after `afterSeq`. A thread's replies are left out and counted
   *  on its first line. */
  messages(
    podId: string,
    channelId: string,
    options: { limit?: number; beforeSeq?: number; afterSeq?: number } = {},
  ) {
    return this.client.request(() =>
      ChannelsService.channelMessageList(
        podId,
        channelId,
        options.beforeSeq,
        options.afterSeq,
        options.limit ?? 50,
      ),
    );
  }

  /** A thread: its first line, and its replies oldest first after `afterSeq`. */
  thread(
    podId: string,
    channelId: string,
    messageId: string,
    options: { limit?: number; afterSeq?: number } = {},
  ) {
    return this.client.request(() =>
      ChannelsService.channelMessageThread(
        podId,
        channelId,
        messageId,
        options.afterSeq,
        options.limit ?? 50,
      ),
    );
  }

  /** Write in a channel. A reply goes where its thread is; otherwise to
   *  `source_id`, or the place the channel last heard from -- unless
   *  `members_only` makes it a note to members. Who it names is read from its
   *  mention parts; a named agent answers. The same `client_message_id` is the
   *  same line. */
  post(
    podId: string,
    channelId: string,
    body: ChannelPostRequest,
    options: { idempotencyKey?: string } = {},
  ) {
    return this.client.request(() =>
      ChannelsService.channelMessagePost(podId, channelId, body, options.idempotencyKey),
    );
  }

  /** Change what one of the caller's own lines says: absent is unchanged,
   *  `parts: null` clears them. Parts sent replace the line's parts, and
   *  name whoever their mentions name, as a post does. */
  edit(podId: string, channelId: string, messageId: string, body: ChannelLineEdit | string) {
    const edit = typeof body === "string" ? { text: body } : body;
    return this.client.request(() =>
      // The generated type flattens `parts` to `null`: the API takes a list.
      ChannelsService.channelMessageUpdate(podId, channelId, messageId, edit as ChannelEditRequest),
    );
  }

  /** Delete one of the caller's own lines; it keeps its place, empty. */
  deleteMessage(podId: string, channelId: string, messageId: string) {
    return this.client.request(() =>
      ChannelsService.channelMessageDelete(podId, channelId, messageId),
    );
  }

  /** Answer what a line asks the caller: `{choice}` or `{text}` for a
   *  question, `{decision}` for an approval, a form's fields. */
  answer(podId: string, channelId: string, messageId: string, answer: Record<string, unknown>) {
    return this.client.request(() =>
      ChannelsService.channelMessageAnswer(podId, channelId, messageId, { answer }),
    );
  }

  /** Share an agent's answer made with the caller's own access with
   *  everybody in the channel (`can_share` on the line). Until then only the
   *  caller reads it; sharing it again changes nothing. */
  share(podId: string, channelId: string, messageId: string) {
    return this.client.request(() =>
      ChannelsService.channelMessageShare(podId, channelId, messageId),
    );
  }

  /** Where a line went outside Lemma, and what became of each send. */
  deliveries(podId: string, channelId: string, messageId: string) {
    return this.client.request(() =>
      ChannelsService.channelDeliveryList(podId, channelId, messageId),
    );
  }

  /** A file a line shares (a `file` part's `file_id`), read as the line's
   *  writer reads it: the bytes, as a `Blob` of the file's content type. */
  file(podId: string, channelId: string, messageId: string, fileId: string): Promise<Blob> {
    const path = [podId, "channels", channelId, "messages", messageId, "files", fileId]
      .map(encodeURIComponent)
      .join("/");
    return this.http.requestBytes("GET", `/pods/${path}`);
  }

  /** Try a failed or given-up send again, now. */
  retryDelivery(podId: string, channelId: string, messageId: string, deliveryId: string) {
    return this.client.request(() =>
      ChannelsService.channelDeliveryRetry(podId, channelId, messageId, deliveryId),
    );
  }

  // -- across the pod ---------------------------------------------------------

  /** What is new for the caller across the pod: the badges, the questions
   *  waiting on them, and the unread lines naming them. */
  inbox(podId: string) {
    return this.client.request(() => ChannelsService.channelInboxGet(podId));
  }

  /** Lines matching every word, newest first, in the channels the caller sees
   *  -- or in one of them -- each with its `rank` and a plain-text `snippet`.
   *  `sender` keeps lines one member, contact or agent said; `since`/`until`
   *  (ISO times) bound when; `archived` searches archived channels too. Page
   *  with `next_cursor`. */
  search(
    podId: string,
    q: string,
    options: {
      channelId?: string;
      kind?: ChannelKind | `${ChannelKind}`;
      sender?: { kind: SenderKind | `${SenderKind}`; id: string };
      since?: string;
      until?: string;
      archived?: boolean;
      cursor?: string;
      limit?: number;
    } = {},
  ) {
    return this.client.request(() =>
      ChannelsService.channelSearch(
        podId,
        q,
        options.channelId,
        options.kind as ChannelKind | undefined,
        options.sender?.kind as SenderKind | undefined,
        options.sender?.id,
        options.since,
        options.until,
        options.archived ?? false,
        options.cursor,
        options.limit ?? 30,
      ),
    );
  }

  /** A contact's channel's cases, the most recently opened first, each with
   *  what happened in it: who answered and when, reopens, how it ended. Page
   *  with `next_cursor`. */
  cases(
    podId: string,
    channelId: string,
    options: { cursor?: string; limit?: number } = {},
  ) {
    return this.client.request(() =>
      ChannelsService.channelCaseList(
        podId,
        channelId,
        options.cursor,
        options.limit ?? 20,
      ),
    );
  }

  /** Who is in a channel -- members, contacts and agents -- in the order they
   *  joined. Page with `next_cursor`. */
  members(
    podId: string,
    channelId: string,
    options: { cursor?: string; limit?: number } = {},
  ) {
    return this.client.request(() =>
      ChannelsService.channelMemberList(
        podId,
        channelId,
        options.cursor,
        options.limit ?? 100,
      ),
    );
  }

  /** Tell another member of the pod something: a line in their own channel.
   *  `to` is a pod member id, user id or email address. */
  notify(podId: string, body: { to: string; text: string }, options: { idempotencyKey?: string } = {}) {
    return this.client.request(() =>
      AgentSurfacesService.agentNoticeSend(
        podId,
        body.to,
        { text: body.text },
        options.idempotencyKey,
      ),
    );
  }
}
