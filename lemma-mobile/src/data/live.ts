import { lemma } from "../session/client";
import type { Server } from "../session/server";
import { askQuestions } from "@web/thread/approval";
import { PLAN_PATH, firstNameOf, ownsOf, parsePlan, planOfOutcome, readMeasure, readableName } from "./model";
import type { ChatLine, LemmaPulse, LemmaSource } from "./source";
import { SOMEBODY, flatAskArgs, items, needOf, readAutomation, readChannel, readDayCount, readPerson, recentChatOf, record, rows, words } from "./wire";
import type { AppItem, Automation, Channel, ConversationRef, DayCount, Lemma, NeedCard, NeedItem, Org, Owns, Starter } from "./types";
import { readText } from "./io";
import { conversationsIn, needsIn } from "./per-lemma";
import { store } from "../session/store";
import { isMissing } from "../areas/admin/words";

/** Main's API, as it is.
 *
 *  What waits on the reader and what moved lately are one read each for the
 *  whole organization (`organizations.needsYou`, `organizations.recents`),
 *  as are its apps and when Home was last looked at. A Lemma's chats with
 *  people and its tables are still read per Lemma: nothing org-wide lists
 *  them. What a Lemma owns is its own `outcome.standing`. */

/** How many of the reader's chats `recents` lists per Lemma. */
const CHATS_PER_LEMMA = 15;

/** The phone's IANA time zone, so `recents` counts the reader's own days. */
function timeZone(): string {
    try {
        return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    } catch {
        return "UTC";
    }
}

async function settled<T>(work: Promise<T>, fallback: T): Promise<T> {
    try {
        return await work;
    } catch {
        return fallback;
    }
}

function appOf(raw: unknown, lemma: Pick<Lemma, "id" | "name">): AppItem | null {
    const app = record(raw);
    const name = words(app.name);
    if (!name) return null;
    return {
        id: words(app.id) ?? name,
        podId: words(app.pod_id) ?? lemma.id,
        podName: words(app.pod_name) ?? lemma.name,
        name,
        label: readableName(name),
        description: words(app.description),
        url: words(app.url),
        updatedAt: words(app.updated_at),
    };
}

export function liveSource(server: Server): LemmaSource {
    const client = (podId?: string) => lemma(server, podId);

    /** What only a Lemma itself lists: its chats with people, its tables. */
    async function ownReads(one: Lemma): Promise<Pick<LemmaPulse, "channels" | "tables">> {
        const pod = client(one.id);
        const [channels, tables] = await Promise.all([
            settled(pod.channels.list(one.id, { archived: false } as never).then((listed) => items(listed).map(readChannel).filter((channel): channel is Channel => channel !== null)), [] as Channel[]),
            settled(pod.tables.list({ limit: 100 } as never).then((listed) => items(listed).map((table) => ({ name: String(record(table).name), updated: words(record(table).updated_at) }))), [] as { name: string; updated: string | null }[]),
        ]);
        return { channels, tables };
    }

    /* A server from before the organization's reads answers them 404: once
       one has, the per-Lemma reads stand in for all of them (`per-lemma.ts`)
       for as long as the app runs, rather than asking again every poll. */
    let older = false;
    async function orFallBack<T>(read: () => Promise<T>, fallback: () => Promise<T>): Promise<T> {
        if (older) return fallback();
        try {
            return await read();
        } catch (problem) {
            if (!isMissing(problem)) throw problem;
            older = true;
            return fallback();
        }
    }

    /** `recents`, by Lemma: its chats, automations and days; null on a server
     *  that has no such read. A read that failed otherwise is no rows rather
     *  than no Lemmas: their chats with people still list. */
    async function recentsByLemma(orgId: string): Promise<Map<string, { conversations: ConversationRef[]; automations: Automation[]; activity: DayCount[] }> | null> {
        const read = await orFallBack<unknown>(
            () => client().organizations.recents(orgId, { days: 14, chats: CHATS_PER_LEMMA, timezone: timeZone() }).catch((problem: unknown) => {
                if (isMissing(problem)) throw problem;
                return null;
            }),
            async () => undefined,
        );
        if (read === undefined) return null;
        return new Map(rows(record(read).pods).map((raw) => {
            const pod = record(raw);
            return [String(pod.pod_id), {
                conversations: rows(pod.chats).map(recentChatOf).filter((one): one is ConversationRef => one !== null),
                automations: rows(pod.automations).map(readAutomation).filter((one): one is Automation => one !== null),
                activity: rows(pod.activity).map(readDayCount).filter((one): one is DayCount => one !== null),
            }];
        }));
    }

    return {
        label: "live",

        async me() {
            const user = record(await client().users.current());
            const first = words(user.first_name);
            const name = [first, words(user.last_name)].filter(Boolean).join(" ") || words(user.email)?.split("@")[0] || "You";
            return { id: words(user.id), email: words(user.email), firstName: first ?? firstNameOf(name === "You" ? null : name), name };
        },

        async orgs(): Promise<Org[]> {
            const listed = await client().organizations.list();
            return items(listed).flatMap((raw) => {
                const org = record(raw);
                const id = words(org.id);
                const name = words(org.name);
                return id && name ? [{ id, name }] : [];
            });
        },

        async lemmas(orgId) {
            const listed = await client().pods.listByOrganization(orgId);
            return items(listed).flatMap((raw) => {
                const pod = record(raw);
                const id = words(pod.id);
                const name = words(pod.name);
                return id && name ? [{ id, name, iconUrl: words(pod.icon_url), description: words(pod.description) }] : [];
            });
        },

        async pulse(orgId, lemmas) {
            const [recents, own] = await Promise.all([recentsByLemma(orgId), Promise.all(lemmas.map(ownReads))]);
            if (!recents) {
                const conversations = await Promise.all(lemmas.map((one) => conversationsIn(client(one.id), one)));
                return lemmas.map((one, index) => ({ lemma: one, conversations: conversations[index], automations: null, activity: null, ...own[index] }));
            }
            return lemmas.map((one, index) => {
                const recent = recents.get(one.id);
                return {
                    lemma: one,
                    conversations: recent?.conversations ?? [],
                    automations: recent?.automations ?? [],
                    activity: recent?.activity ?? [],
                    ...own[index],
                };
            });
        },

        /* Unlike the rows, a failed read here is said: an empty queue reads
           as "nothing waits on you". */
        async needs(orgId, lemmas) {
            const now = Date.now();
            return orFallBack(
                async () => items(await client().organizations.needsYou(orgId)).map((raw) => needOf(raw, now)).filter((item): item is NeedItem => item !== null),
                async () => (await Promise.all(lemmas.map((one) => needsIn(client(one.id), one, now)))).flat(),
            );
        },

        /* What it owns is the Lemma's own, kept on the server; a Lemma that
           keeps nothing there yet is read from its plan file, as the web does. */
        async owns(one): Promise<Owns> {
            const standing = record(await settled(client(one.id).pods.outcome.standing(one.id), null));
            const outcome = standing.outcome && typeof standing.outcome === "object" ? record(standing.outcome) : null;
            const sentence = words(outcome?.sentence);
            if (outcome && sentence) {
                const plan = planOfOutcome({
                    sentence,
                    kind: String(outcome.kind ?? "week"),
                    target: typeof outcome.target === "number" ? outcome.target : null,
                    unit: words(outcome.unit),
                    due: words(outcome.due),
                    measure: words(outcome.measure),
                });
                const value = typeof standing.value === "number" && Number.isFinite(standing.value) ? standing.value : null;
                const owns = ownsOf(plan, value, Date.now());
                const problem = words(standing.problem);
                return problem && owns.display === null ? { ...owns, note: problem } : owns;
            }
            const text = await readText(server, one.id, PLAN_PATH);
            const plan = text ? parsePlan(text) : null;
            let value: number | null = null;
            if (plan?.measure) {
                value = await settled(client(one.id).datastore.query(plan.measure).then((answer) => readMeasure(items(answer) as Record<string, unknown>[])), null);
            }
            return ownsOf(plan, value, Date.now());
        },

        /* Each app names its own Lemma; the server lists them newest first. */
        async apps(orgId, lemmas) {
            return orFallBack(
                async () => items(await client().organizations.apps(orgId)).map((raw) => appOf(raw, { id: "", name: "" })).filter((app): app is AppItem => app !== null && app.podId !== ""),
                async () => {
                    const lists = await Promise.all(lemmas.map((one) => settled(
                        client(one.id).apps.list({ limit: 50 } as never).then((listed) => items(listed).map((raw) => appOf(raw, one)).filter((app): app is AppItem => app !== null)),
                        [] as AppItem[],
                    )));
                    return lists.flat().sort((a, b) => (b.updatedAt ?? "").localeCompare(a.updatedAt ?? ""));
                },
            );
        },

        async card(item): Promise<NeedCard> {
            const open = (label: string): NeedCard => ({ ...item, note: item.detail, answer: { kind: "open", label } });
            if (item.kind === "CONTACT") return open("Reply");
            if (item.kind === "FORM") return open("Open the form");
            if (item.kind === "DECISION") return open("Decide");
            /* A question a Lemma asked in your chat with it: its choices are
               the answers, when it offered a few. */
            if (item.kind === "MESSAGE") {
                if (item.channelId && item.messageId && item.choices.length >= 2 && item.choices.length <= 4) {
                    return { ...item, note: item.detail, answer: { kind: "choose", questionId: "choice", options: item.choices } };
                }
                return open("Answer");
            }
            if (!item.conversationId || !item.toolCallId) return open(item.kind === "QUESTION" ? "Answer" : "Open");
            if (item.kind === "APPROVAL" || item.kind === "PERMISSION") {
                return { ...item, note: item.detail, answer: { kind: "approve", approve: "Approve", decline: "Not this one" } };
            }
            /* A question: answered here only when it is one question with a
               few choices. Anything longer opens the chat. */
            const listed = await settled(client(item.podId).conversations.messages.list(item.conversationId, { pod_id: item.podId, limit: 40 } as never), null);
            const call = items(listed).map(record).find((message) => message.tool_call_id === item.toolCallId && message.tool_args !== undefined);
            const questions = call ? askQuestions(flatAskArgs(call.tool_args)) : [];
            const only = questions.length === 1 ? questions[0] : null;
            if (only && !only.multiSelect && only.options.length >= 2 && only.options.length <= 4) {
                return { ...item, title: only.question || item.title, note: null, answer: { kind: "choose", questionId: only.header, options: only.options.map((option) => option.label) } };
            }
            return open("Answer");
        },

        async answer(item, decision, answers) {
            if (item.kind === "MESSAGE") {
                const choice = answers ? words(Object.values(answers)[0]) : null;
                if (!item.channelId || !item.messageId || !choice) throw new Error("This one is answered in its chat.");
                await client(item.podId).channels.answer(item.podId, item.channelId, item.messageId, { choice });
                return;
            }
            if (!item.conversationId || !item.toolCallId) throw new Error("This one is answered in its chat.");
            await client(item.podId).conversations.approvals.resolve(
                item.conversationId,
                item.toolCallId,
                { decision, response: answers ? { answers } : {} } as never,
                { pod_id: item.podId } as never,
            );
        },

        async runs(one) {
            const pod = client(one.id);
            const [apps, tables, flows, schedules] = await Promise.all([
                settled(pod.apps.list({ limit: 12 } as never).then((listed) => items(listed).map((raw) => appOf(raw, one)).filter((app): app is AppItem => app !== null)), [] as AppItem[]),
                settled(pod.tables.list({ limit: 20 } as never).then((listed) => items(listed).map((raw) => String(record(raw).name))), [] as string[]),
                settled(pod.workflows.list({ limit: 20 } as never).then((listed) => items(listed).map(record).filter((flow) => flow.is_active !== false).map((flow) => String(flow.name))), [] as string[]),
                settled(pod.schedules.list({ limit: 20 } as never).then((listed) => items(listed).map(record).filter((job) => job.is_active !== false && job.internal !== true).map((job) => String(job.name ?? job.title ?? ""))), [] as string[]),
            ]);
            return {
                apps,
                tables: tables.filter(Boolean).map((name) => ({ name, label: readableName(name) })),
                automations: [...flows, ...schedules].filter(Boolean).map((name) => ({ name, label: readableName(name) })),
            };
        },

        async starters(one): Promise<Starter[]> {
            const listed = await settled(client(one.id).podSurfaces.list(one.id, { limit: 30 } as never), { items: [] } as unknown);
            const mine = items(listed).map(record).filter((surface) => surface.uses_default_agent === true || surface.agent_name === "pod_default" || surface.agent_name === "POD_DEFAULT");
            const ready = (platform: string) => mine.some((surface) => String(surface.platform).toUpperCase() === platform && (surface.status ?? "ACTIVE") === "ACTIVE");
            return [
                { platform: "GROUP", label: "Group chat", ready: true, note: `${one.name} answers when named` },
                { platform: "WHATSAPP", label: "WhatsApp group", ready: ready("WHATSAPP"), note: null },
                { platform: "SLACK", label: "Slack channel", ready: ready("SLACK"), note: null },
                { platform: "TELEGRAM", label: "Telegram group", ready: ready("TELEGRAM"), note: null },
            ];
        },

        async lines(podId, channelId) {
            const pod = client(podId);
            const me = await settled(this.me(), null);
            const [detail, page] = await Promise.all([
                pod.channels.get(podId, channelId),
                pod.channels.messages(podId, channelId, { limit: 50 } as never),
            ]);
            const channel = readChannel(detail);
            const lines: ChatLine[] = items(page).flatMap((raw) => {
                const line = record(raw);
                const sender = readPerson(line.sender) ?? SOMEBODY;
                const text = line.deleted === true || line.withheld === true ? null : words(line.text);
                if (!text) return [];
                const at = Date.parse(String(line.created_at ?? ""));
                return [{
                    id: String(line.id),
                    who: sender.name ?? (sender.kind === "AGENT" ? "The Lemma" : "Someone"),
                    whoKind: sender.kind,
                    mine: sender.kind === "MEMBER" && me !== null && sender.id === me.id,
                    text,
                    at: Number.isNaN(at) ? null : at,
                }];
            });
            lines.sort((a, b) => (a.at ?? 0) - (b.at ?? 0));
            const title = channel ? (channel.kind === "CONTACT" ? channel.person?.name ?? channel.title ?? "A contact" : channel.kind === "DIRECT" ? (channel.sources.length ? [...new Set(channel.sources.map((source) => source.platform))].map((platform) => platform.charAt(0) + platform.slice(1).toLowerCase()).join(", ") : "Notices") : (channel.title ?? "A group").replace(/^#/, "")) : "Chat";
            return { title, kind: channel?.kind ?? "GROUP", lines };
        },

        async say(podId, channelId, text) {
            await client(podId).channels.post(podId, channelId, { text } as never);
        },

        /* Kept by the server, so the web and the phone agree on it. */
        async homeSeen(orgId) {
            return orFallBack(async () => {
                const seen = record(await client().organizations.homeSeen.get(orgId));
                const at = Date.parse(words(seen.seen_at) ?? "");
                return Number.isNaN(at) ? null : at;
            }, () => store.homeSeen(orgId));
        },
        async markHomeSeen(orgId, at) {
            await orFallBack(async () => { await client().organizations.homeSeen.mark(orgId, new Date(at).toISOString()); }, () => store.setHomeSeen(orgId, at));
        },
    };
}
