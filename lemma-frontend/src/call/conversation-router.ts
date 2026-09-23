import { readSSE, parseSSEJson, parseAssistantStreamEvent, upsertConversationMessage, type LemmaClient, type Conversation, type ConversationMessage } from "lemma-sdk";
import { buildTurns } from "@/thread/turns";
import { resourceLabel } from "@/thread/display-resource";
import { NEW_CONVERSATION } from "@/data/types";
import { classifyCall, type ConversationSnapshot, type RouterState, type VoiceEvent } from "./routing";

export const running = (status: string) => ["RUNNING", "IN_PROGRESS", "PROCESSING", "STOP_REQUESTED"].includes(status.toUpperCase());
export function snapshotOf(record: Conversation, messages: ConversationMessage[]): ConversationSnapshot {
    const ordered = [...messages].sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0) || a.created_at.localeCompare(b.created_at));
    const items = buildTurns(ordered).flatMap(t => t.items);
    return {
        id: record.id, title: record.title || "Untitled conversation", status: record.status || "UNKNOWN",
        updatedAt: record.updated_at, fetchedAt: new Date().toISOString(), lastRunStatus: record.last_run_status ?? null,
        error: record.last_run_error ?? null,
        messages: ordered.filter(m => m.kind === "TEXT" || m.kind === "NOTIFICATION")
            .slice(-40).map(m => ({ role: m.role, text: (m.text ?? "").slice(0, 4000), at: m.created_at })),
        plan: items.flatMap(i => i.kind === "plan" ? [i.steps] : []).at(-1) ?? [],
        resources: items.flatMap(i => i.kind === "resource" ? [{ type: i.resource.type, label: resourceLabel(i.resource) }] : []),
        openQuestions: items.flatMap(i => i.kind === "interaction" && i.interaction.open ? [i.interaction] : []),
        needsInput: items.some(i => i.kind === "interaction" && i.interaction.open),
    };
}
export function snapshotText(snapshot: ConversationSnapshot): string {
    // Voice gets current evidence, never a replay of another conversation.
    const lastUser = snapshot.messages.map(m => m.role).lastIndexOf("user");
    const answer = snapshot.partialText || snapshot.messages.slice(lastUser + 1).filter(m => m.role === "assistant").at(-1)?.text;
    return JSON.stringify({ task: snapshot.title, status: snapshot.status,
        currentResult: answer?.slice(-6000), error: snapshot.error,
        plan: snapshot.plan, openQuestions: snapshot.openQuestions,
        resources: snapshot.resources.slice(-5) });
}
/** Owns routing and multiple background conversations for one live call. Closing
 * detaches observation; it never cancels work already accepted by the backend. */
export class ConversationRouter {
    snapshots = new Map<string, ConversationSnapshot>();
    messages = new Map<string, ConversationMessage[]>();
    focusedId: string | null = null;
    private epoch = 0;
    private active = false;
    private streams = new Map<string, AbortController>();
    private records = new Map<string, Conversation>();
    private partials = new Map<string, string>();
    private controller = new AbortController();
    private listeners = new Set<(event: VoiceEvent) => void>();
    private transcript = "";
    private podContext = "";
    private requests = new Set<string>();
    private recentDispatches = new Map<string, { at: number; target: string }>();
    private publishedProgress = new Map<string, string>();
    private serial: Promise<unknown> = Promise.resolve();
    private monitored = new Set<string>();
    private progressTimers = new Map<string, ReturnType<typeof setTimeout>>();
    private responseRequests = new Map<string, string>();
    private pendingRuns = new Map<string, string>();
    private refreshing: Promise<void> | null = null;
    private recentEvents: VoiceEvent[] = [];
    private waitingEvents: VoiceEvent[] = [];
    private client: LemmaClient;
    private podId: string;
    private changed: () => void;
    private classify: typeof classifyCall;
    constructor(client: LemmaClient, podId: string, changed: () => void, classify = classifyCall) {
        this.client = client; this.podId = podId; this.changed = changed; this.classify = classify;
    }
    subscribe = (listener: (event: VoiceEvent) => void) => {
        this.listeners.add(listener);
        const waiting = this.waitingEvents; this.waitingEvents = [];
        for (const event of waiting) listener(event);
        return () => { this.listeners.delete(listener); };
    };
    observeTranscript = (text: string) => { this.transcript = text; };
    setContext = (text: string) => { this.podContext = text; };
    private emit(event: VoiceEvent) {
        if (!this.active) return;
        this.recentEvents = [...this.recentEvents, { ...event, text: event.text.slice(0, 2000) }].slice(-12);
        if (!this.listeners.size) this.waitingEvents = [...this.waitingEvents, event].slice(-24);
        for (const listener of this.listeners) listener(event);
    }
    private state(utterance: string, event?: VoiceEvent): RouterState {
        // A generous but bounded context. Keep every candidate's identity and
        // newest messages, then distribute the remaining text budget fairly.
        const all = [...this.snapshots.values()];
        const perConversation = Math.floor(300000 / Math.max(1, all.length));
        return { mode: event ? "event" : "utterance", utterance, transcript: this.transcript.slice(-90000), podContext: this.podContext,
            focusedConversationId: this.focusedId, recentEvents: this.recentEvents, conversations: all.map(s => {
                let remaining = perConversation;
                const messages = [...s.messages].reverse().flatMap(m => {
                    if (remaining <= 0) return [];
                    const text = m.text.slice(0, remaining); remaining -= text.length;
                    return [{ ...m, text }];
                }).reverse();
                return { ...s, messages };
            }), ...(event ? { event } : {}) };
    }
    async open(selectedId: string | null) {
        if (this.active) return this.focusedId;
        this.active = true; this.controller = new AbortController();
        this.focusedId = selectedId && selectedId !== NEW_CONVERSATION ? selectedId : null;
        const epoch = this.epoch;
        try {
            await this.refreshCatalogue();
            if (epoch !== this.epoch) return null;
            if (this.focusedId) this.monitored.add(this.focusedId);
            // Other conversations inform routing, but only call targets announce updates.
            if (this.focusedId && running(this.snapshots.get(this.focusedId)?.status ?? "")) this.watch(this.focusedId);
            return this.focusedId;
        } catch (error) { if (epoch === this.epoch) this.close(); throw error; }
    }
    private async read(id: string, epoch = this.epoch) {
        const [record, page] = await Promise.all([
            this.client.conversations.get(id, { pod_id: this.podId }),
            this.client.conversations.messages.list(id, { pod_id: this.podId, limit: 80 }),
        ]);
        if (epoch !== this.epoch || !this.active) throw new Error("Call ended");
        this.records.set(id, record);
        const snapshot = snapshotOf(record, page.items);
        this.snapshots.set(id, snapshot); this.messages.set(id, page.items); this.changed();
        return snapshot;
    }
    private async refreshCatalogue() {
        if (this.refreshing) return this.refreshing;
        const epoch = this.epoch;
        const request = (async () => {
            const page = await this.client.conversations.list({ pod_id: this.podId, archived: false, limit: 16 });
            if (epoch !== this.epoch || !this.active) return;
            for (const record of page.items) {
                this.records.set(record.id, record);
                this.snapshots.set(record.id, snapshotOf(record, []));
            }
            // Fetch history once for the call's focus. Other histories are lazy.
            if (this.focusedId) await this.read(this.focusedId, epoch);
            this.changed();
        })();
        this.refreshing = request;
        try { await request; } finally { if (this.refreshing === request) this.refreshing = null; }
    }
    route = (id: string, text: string, transcript: string) => {
        if (!this.active || this.requests.has(id)) return Promise.resolve();
        this.requests.add(id);
        const epoch = this.epoch;
        const task = this.serial.catch(() => {}).then(async () => {
            if (epoch !== this.epoch || !this.active) return;
            this.transcript = transcript;
            const decision = await this.classify(this.state(text), this.controller.signal);
            if (epoch !== this.epoch || !this.active) return;
            if (decision.action === "voice") return;
            if (decision.action === "clarify") {
                this.emit({ id, kind: "clarify", conversationId: null, speak: true,
                    text: `No action was taken. Ask one short question to clarify the user's intent or which conversation they mean. User said: ${text}` });
                return;
            }
            const fingerprint = text.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
            const previous = this.recentDispatches.get(fingerprint);
            if ((decision.action === "new" || decision.action === "existing") && previous && Date.now() - previous.at < 15000
                && (decision.action === "new" || decision.conversationId === previous.target)) {
                this.responseRequests.set(previous.target, id);
                this.emit({ id, kind: "accepted", conversationId: previous.target, speak: false,
                    text: "This same request was already accepted. Continue following its existing work; no duplicate was sent." });
                return;
            }
            let target = decision.conversationId;
            if (decision.action === "new") {
                const created = await this.client.conversations.create({ pod_id: this.podId });
                if (epoch !== this.epoch || !this.active) return;
                target = created.id;
                this.records.set(target, created);
                this.messages.set(target, []);
                this.snapshots.set(target, snapshotOf(created, []));
            }
            if (!target || !this.snapshots.has(target)) throw new Error("The conversation is no longer available. Nothing was sent.");
            this.focusedId = target; this.monitored.add(target); this.changed();
            if (decision.action === "snapshot") {
                this.responseRequests.set(target, id);
                const snapshot = this.streams.has(target) ? this.snapshots.get(target)! : await this.read(target, epoch);
                if (running(snapshot.status)) this.watch(target);
                this.emit({ id, kind: "snapshot", conversationId: target, speak: true, responseTo: id, text: snapshotText(snapshot) });
                return;
            }
            // Read permission and current state again before sending. appendMessage
            // starts a run if idle and joins it if running (SDK controller contract).
            if (!this.messages.has(target)) await this.read(target, epoch);
            if (epoch !== this.epoch || !this.active) return;
            const result = await this.client.conversations.appendMessage(target, {
                content: `Reference only: earlier requests in this exchange are already handled separately. Execute only the latest instruction below.\n${transcript.slice(-4000)}\n\nLatest user instruction:\n${text}`,
            }, { pod_id: this.podId, signal: this.controller.signal });
            if (epoch !== this.epoch || !this.active) return;
            this.recentDispatches.set(fingerprint, { at: Date.now(), target });
            this.responseRequests.set(target, id);
            this.pendingRuns.set(target, result.agent_run_id);
            const record = this.records.get(target);
            if (record) this.records.set(target, { ...record, status: "RUNNING" } as Conversation);
            this.watch(target, result.agent_run_id);
            this.emit({ id, kind: "accepted", conversationId: target, speak: false,
                text: `Work on ${this.snapshots.get(target)?.title} accepted. ${result.started_new_run ? "Started work." : "Added to existing work."} User request: ${text}` });
            this.changed();
        }).catch(error => {
            if (epoch !== this.epoch || !this.active) return;
            this.emit({ id, kind: "failed", conversationId: null, speak: true,
                text: error instanceof Error ? error.message : "The request could not be routed. Do not claim it succeeded." });
        });
        this.serial = task;
        return task;
    };
    private watch(id: string, runId?: string) {
        if (this.streams.has(id) || !this.active) return;
        const controller = new AbortController();
        this.streams.set(id, controller);
        const epoch = this.epoch;
        void this.consume(id, controller, epoch, runId).finally(() => {
            if (this.streams.get(id) === controller) this.streams.delete(id);
            if (epoch === this.epoch) {
                this.changed();
                const nextRun = this.pendingRuns.get(id);
                if (nextRun && nextRun !== runId && this.active) this.watch(id, nextRun);
            }
        });
    }
    private rebuild(id: string) {
        const record = this.records.get(id);
        if (!record) return;
        const snapshot = snapshotOf(record, this.messages.get(id) ?? []);
        snapshot.partialText = this.partials.get(id) ?? "";
        this.snapshots.set(id, snapshot);
        this.changed();
    }
    private async consume(id: string, controller: AbortController, epoch: number, runId?: string) {
        // Reconnect the SSE transport only. Never poll messages while waiting.
        for (let attempt = 0; attempt < 3 && !controller.signal.aborted; attempt++) {
            let terminal = false;
            try {
                const stream = await this.client.conversations.resumeStream(id, { pod_id: this.podId, signal: controller.signal, agent_run_id: runId });
                for await (const frame of readSSE(stream)) {
                    if (controller.signal.aborted || epoch !== this.epoch) return;
                    const parsed = parseAssistantStreamEvent(parseSSEJson(frame));
                    if (parsed.interrupted) break;
                    if (parsed.conversationId && parsed.conversationId !== id) continue;
                    const record = this.records.get(id);
                    if (!record) continue;
                    if (parsed.token && (!parsed.tokenKind || parsed.tokenKind === "text")) {
                        this.partials.set(id, ((this.partials.get(id) ?? "") + parsed.token).slice(-20000));
                    }
                    if (parsed.message) {
                        this.messages.set(id, upsertConversationMessage(this.messages.get(id) ?? [], parsed.message));
                        if (parsed.message.role === "assistant" && parsed.message.kind === "TEXT") this.partials.delete(id);
                    }
                    if (parsed.status || parsed.title || parsed.error) this.records.set(id, {
                        ...record, ...(parsed.status ? { status: parsed.status } : {}), ...(parsed.title ? { title: parsed.title } : {}),
                        ...(parsed.error ? { status: "FAILED", last_run_error: parsed.error } : {}), updated_at: new Date().toISOString(),
                    } as Conversation);
                    this.rebuild(id);
                    terminal = Boolean(parsed.error || (parsed.status && !running(parsed.status)));
                    if (terminal) {
                        clearTimeout(this.progressTimers.get(id)); this.progressTimers.delete(id);
                        if (!runId || this.pendingRuns.get(id) === runId) this.pendingRuns.delete(id);
                        const snapshot = this.snapshots.get(id)!;
                        const kind = snapshot.needsInput ? "needs_input" : parsed.error || snapshot.status === "FAILED" ? "failed" : "completed";
                        await this.selectEvent(id, kind, epoch);
                        return;
                    }
                    if ((parsed.token || parsed.message) && !this.progressTimers.has(id)) {
                        this.progressTimers.set(id, setTimeout(() => {
                            this.progressTimers.delete(id);
                            const current = this.snapshots.get(id);
                            if (current && epoch === this.epoch && !controller.signal.aborted) {
                                const text = snapshotText(current);
                                if (this.publishedProgress.get(id) !== text) {
                                    this.publishedProgress.set(id, text);
                                    this.emit({ id: crypto.randomUUID(), conversationId: id, kind: "progress", speak: false, text });
                                }
                            }
                        }, 6000));
                    }
                    // Persisted messages carry resources/plan changes. Token deltas
                    // update the cached snapshot and screen without another request.
                    if (parsed.message && this.snapshots.get(id)?.needsInput) {
                        this.pendingRuns.delete(id);
                        await this.selectEvent(id, "needs_input", epoch);
                    }
                }
                if (terminal) return;
            } catch { if (controller.signal.aborted || epoch !== this.epoch) return; }
            if (attempt < 2) await new Promise<void>(resolve => {
                const done = () => { clearTimeout(timer); controller.signal.removeEventListener("abort", done); resolve(); };
                const timer = setTimeout(done, 1000 * 2 ** attempt);
                controller.signal.addEventListener("abort", done, { once: true });
            });
        }
        if (epoch === this.epoch && !controller.signal.aborted) {
            this.pendingRuns.delete(id);
            this.emit({ id: crypto.randomUUID(), conversationId: id, kind: "failed", speak: true,
                text: "The work stream disconnected. The call is still active; work may still be running." });
        }
    }
    private async selectEvent(id: string, kind: VoiceEvent["kind"], epoch: number) {
        const snapshot = this.snapshots.get(id);
        if (!snapshot) return;
        const event: VoiceEvent = { id: crypto.randomUUID(), conversationId: id, kind, responseTo: this.responseRequests.get(id), speak: false, text: snapshotText(snapshot) };
        let delivery: "speak" | "context" | "ignore" = kind === "progress" ? "context" : "speak";
        try { delivery = (await this.classify(this.state("", event), this.controller.signal)).delivery; } catch { /* Deliver verified terminal state even if selection fails. */ }
        if (epoch === this.epoch && delivery !== "ignore") this.emit({ ...event, speak: delivery === "speak" && !!event.responseTo });
    }
    close = () => {
        this.active = false; this.epoch++; this.controller.abort();
        for (const stream of this.streams.values()) stream.abort();
        for (const timer of this.progressTimers.values()) clearTimeout(timer);
        this.progressTimers.clear(); this.responseRequests.clear(); this.recentDispatches.clear(); this.publishedProgress.clear();
        this.streams.clear(); this.records.clear(); this.partials.clear(); this.focusedId = null; this.monitored.clear(); this.pendingRuns.clear();
        this.requests.clear(); this.snapshots.clear(); this.messages.clear(); this.transcript = "";
        this.recentEvents = []; this.waitingEvents = []; this.refreshing = null; this.serial = Promise.resolve(); this.changed();
    };
    get isRunning() { return this.pendingRuns.size > 0 || [...this.monitored].some(id => running(this.snapshots.get(id)?.status ?? "")); }
}
