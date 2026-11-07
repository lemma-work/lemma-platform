import { lemma } from "../session/client";
import type { Server } from "../session/server";
import { askQuestions } from "@web/thread/approval";
import { PLAN_PATH, firstNameOf, ownsOf, parsePlan, readMeasure, readableName, type PendingAsk, type WaitRow } from "./model";
import type { ChatLine, LemmaPulse, LemmaSource } from "./source";
import { SOMEBODY, conversationRefOf, flatAskArgs, items, readChannel, readPerson, record, words } from "./wire";
import type { AppItem, Channel, ConversationRef, Lemma, NeedCard, Org, Owns, Starter } from "./types";
import { store } from "../session/store";
import { readText } from "./io";

/** Main's API, as it is, read per Lemma.
 *
 *  This is the one module the open backend PRs shrink. `pulse` fans out six
 *  reads per Lemma; #1045 replaces it with `organizations.needsYou` and
 *  `organizations.recents` (one request each for the whole org), `apps`
 *  becomes `organizations.apps`, `homeSeen` becomes `homeSeen.get/mark`,
 *  and #1046 turns `owns` into `config.outcome` + `outcome/standing`. Each
 *  of those keeps the shapes in `types.ts`, so no screen changes. */

function readWaits(listed: unknown, names: ReadonlyMap<string, string>): WaitRow[] {
    return items(listed).flatMap((entry) => {
        const wait = record(record(entry).wait);
        const run = record(record(entry).run);
        const id = words(wait.id);
        const runId = words(wait.run_id) ?? words(run.id);
        /* A form waits as HUMAN (`WorkflowRunWaitType`): the queue's FORM. */
        const said = words(wait.wait_type);
        const type = said === "HUMAN" || said === "FORM" ? "FORM" : said === "DECISION" ? "DECISION" : null;
        if (!id || !runId || !type) return [];
        const schema = record(record(wait.payload).schema);
        const answer = record(record(schema.properties).answer);
        return [{
            id,
            type,
            question: words(answer.description),
            createdAt: words(wait.created_at),
            runId,
            workflowName: names.get(words(run.workflow_id) ?? "") ?? "a workflow",
        }];
    });
}

async function settled<T>(work: Promise<T>, fallback: T): Promise<T> {
    try {
        return await work;
    } catch {
        return fallback;
    }
}

function appOf(raw: unknown, lemma: Lemma): AppItem | null {
    const app = record(raw);
    const name = words(app.name);
    if (!name) return null;
    return {
        id: words(app.id) ?? name,
        podId: lemma.id,
        podName: lemma.name,
        name,
        label: readableName(name),
        description: words(app.description),
        url: words(app.url),
        updatedAt: words(app.updated_at),
    };
}

export function liveSource(server: Server): LemmaSource {
    const client = (podId?: string) => lemma(server, podId);

    async function pulseOf(one: Lemma): Promise<LemmaPulse> {
        const pod = client(one.id);
        const [conversations, asks, waits, channels, contacts, tables] = await Promise.all([
            settled(pod.conversations.list({ pod_id: one.id, limit: 25, in_channel: false } as never).then((listed) => items(listed).map(conversationRefOf)), [] as ConversationRef[]),
            settled(pod.conversations.pendingInteractions({ pod_id: one.id, limit: 100 } as never).then((listed) => items(listed) as PendingAsk[]), [] as PendingAsk[]),
            settled((async () => {
                const owed = await pod.workflows.runs.waitingAssignedToMe({ limit: 50 } as never);
                if (items(owed).length === 0) return [] as WaitRow[];
                const flows = await settled(pod.workflows.list({ limit: 100 } as never), { items: [] } as unknown);
                const names = new Map(items(flows).map((flow) => [words(record(flow).id) ?? "", readableName(words(record(flow).name) ?? "a workflow")]));
                return readWaits(owed, names);
            })(), [] as WaitRow[]),
            settled(pod.channels.list(one.id, { archived: false } as never).then((listed) => items(listed).map(readChannel).filter((channel): channel is Channel => channel !== null)), [] as Channel[]),
            settled(pod.channels.list(one.id, { kind: "CONTACT", archived: false, needsPerson: true } as never).then((listed) => items(listed).map(readChannel).filter((channel): channel is Channel => channel !== null)), [] as Channel[]),
            settled(pod.tables.list({ limit: 100 } as never).then((listed) => items(listed).map((table) => ({ name: String(record(table).name), updated: words(record(table).updated_at) }))), [] as { name: string; updated: string | null }[]),
        ]);
        return { lemma: one, conversations, asks, waits, channels, contacts, tables };
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

        async pulse(_orgId, lemmas) {
            return Promise.all(lemmas.map(pulseOf));
        },

        async owns(one): Promise<Owns> {
            const text = await readText(server, one.id, PLAN_PATH);
            const plan = text ? parsePlan(text) : null;
            let value: number | null = null;
            if (plan?.measure) {
                value = await settled(client(one.id).datastore.query(plan.measure).then((answer) => readMeasure(items(answer) as Record<string, unknown>[])), null);
            }
            return ownsOf(plan, value, Date.now());
        },

        async apps(_orgId, lemmas) {
            const lists = await Promise.all(lemmas.map(async (one) => settled(
                client(one.id).apps.list({ limit: 50 } as never).then((listed) => items(listed).map((raw) => appOf(raw, one)).filter((app): app is AppItem => app !== null)),
                [] as AppItem[],
            )));
            return lists.flat().sort((a, b) => (b.updatedAt ?? "").localeCompare(a.updatedAt ?? ""));
        },

        async card(item): Promise<NeedCard> {
            const open = (label: string): NeedCard => ({ ...item, note: item.detail, answer: { kind: "open", label } });
            if (item.kind === "CONTACT") return open("Reply");
            if (item.kind === "FORM") return open("Open the form");
            if (item.kind === "DECISION") return open("Decide");
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

        homeSeen: (orgId) => store.homeSeen(orgId),
        markHomeSeen: (orgId, at) => store.setHomeSeen(orgId, at),
    };
}
