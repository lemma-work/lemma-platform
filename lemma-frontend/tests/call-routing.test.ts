import test from "node:test";
import assert from "node:assert/strict";
import { decisionFrom, decisionRequest, evidenceFor, EVIDENCE_CHARS, questionsFor } from "../src/call/jev-router.ts";
import { ConversationRouter } from "../src/call/conversation-router.ts";
import { UtteranceBuffer } from "../src/call/utterance-buffer.ts";
import { classifyCall, type RouteAsk, type RouterState, type VoiceEvent } from "../src/call/routing.ts";

const state: RouterState = { mode: "utterance", utterance: "What's up with that?", transcript: "User: Research Indian companies", podContext: "Research pod",
    focusedConversationId: "one", conversations: [{ id: "one", title: "Indian companies", status: "RUNNING", updatedAt: "2026-09-17", fetchedAt: "2026-09-17",
        lastRunStatus: "RUNNING", error: null, messages: [], plan: [], resources: [], needsInput: false, openQuestions: [] }] };
const eventState: RouterState = { ...state, mode: "event", utterance: "", event: { id: "event", conversationId: "one", kind: "completed", text: "Finished", speak: false } };
/** Answers as `DecisionResponse.answers` carries them: System One's with a
 *  confidence, the model's with none, and a fallback nothing committed to. */
const systemOne = (value: string, confidence = 0.95) => ({ value, by: "system_one", distribution: { [value]: confidence }, confidence, abstained: false });
const model = (value: string) => ({ value, by: "model", distribution: null, confidence: null, abstained: false });
const fallback = (value: string) => ({ value, by: "model", distribution: null, confidence: null, abstained: true });
const UNAVAILABLE = "Call routing is unavailable. No request was dispatched.";
type Requested = { method: string; path: string; body: any; signal?: AbortSignal };
function backend(answer: (body: any, signal?: AbortSignal) => unknown) {
    const requests: Requested[] = [];
    const client = { request: async (method: string, path: string, options?: { body?: unknown; signal?: AbortSignal }) => {
        requests.push({ method, path, body: options?.body, signal: options?.signal });
        return answer(options?.body, options?.signal);
    } } as unknown as RouteAsk["client"];
    return { client, requests };
}

test("routing keeps selected choices regardless of confidence; missing targets do not dispatch", () => {
    assert.equal(decisionFrom(state, { action: systemOne("snapshot"), target: systemOne("c0") }).conversationId, "one");
    assert.equal(decisionFrom(state, { action: systemOne("existing"), target: systemOne("c0", 0.3) }).action, "existing");
    assert.equal(decisionFrom(state, { action: systemOne("snapshot"), target: systemOne("none") }).action, "clarify");
    assert.throws(() => decisionFrom(state, { action: systemOne("existing"), target: systemOne("invented-id") }));
    assert.equal(decisionFrom(state, { action: systemOne("new"), target: systemOne("none") }).action, "new");
    // System One's confidence travels with the route: the lower of the two when both decide it.
    assert.equal(decisionFrom(state, { action: systemOne("existing", 0.9), target: systemOne("c0", 0.7) }).confidence, 0.7);
});

test("a model's route has no confidence, and an unresolved target is never guessed", () => {
    assert.deepEqual(decisionFrom(state, { action: model("existing"), target: model("c0") }),
        { action: "existing", conversationId: "one", delivery: "context", confidence: null });
    assert.equal(decisionFrom(state, { action: model("voice"), target: model("none") }).action, "voice");
    // The target's fallback, taken because nothing committed, is not a destination.
    assert.equal(decisionFrom(state, { action: model("snapshot"), target: fallback("none") }).action, "clarify");
    // An action nobody decided dispatches nothing.
    assert.throws(() => decisionFrom(state, { target: model("c0") }));
    // With no conversation open the target is not asked: work for one needs clarifying, new work does not.
    const empty: RouterState = { ...state, focusedConversationId: null, conversations: [] };
    assert.equal(decisionFrom(empty, { action: model("existing") }).action, "clarify");
    assert.equal(decisionFrom(empty, { action: model("new") }).action, "new");
});

test("event selection has no execution route; System One is held to its bar and only a model's speak stands", () => {
    assert.deepEqual(Object.keys(questionsFor(eventState)), ["delivery"]);
    const delivery = (answer: unknown) => decisionFrom(eventState, { delivery: answer });
    assert.deepEqual(delivery(systemOne("speak", 0.9)), { action: "voice", conversationId: "one", delivery: "speak", confidence: 0.9 });
    assert.equal(delivery(systemOne("ignore", 0.65)).delivery, "ignore");
    assert.equal(delivery(systemOne("speak", 0.3)).delivery, "context");
    assert.equal(delivery(fallback("context")).delivery, "context");
    assert.deepEqual(delivery(model("speak")), { action: "voice", conversationId: "one", delivery: "speak", confidence: null });
    assert.equal(delivery(model("ignore")).delivery, "context");
    assert.equal(delivery(model("context")).delivery, "context");
    assert.throws(() => decisionFrom(eventState, {}));
    assert.throws(() => delivery({ value: "speak", by: "system_one", confidence: 1.5, abstained: false }));
});

test("the backend is asked the same questions as an inline decider, in the call's pod", async () => {
    const { client, requests } = backend(() => ({ id: "d", answers: { action: systemOne("snapshot"), target: systemOne("c0") }, open: [], status: "answered", trace: [] }));
    const result = await classifyCall(state, { client, podId: "pod-1" });
    assert.deepEqual(result, { action: "snapshot", conversationId: "one", delivery: "context", confidence: 0.95 });
    assert.equal(requests.length, 1);
    const [{ method, path, body }] = requests;
    assert.equal(method, "POST");
    assert.equal(path, "/pods/pod-1/decisions");
    assert.deepEqual(Object.keys(body).sort(), ["definition", "options", "record", "state", "visibility"]);
    // A call's transcript and conversations are never kept, and never the pod's.
    assert.equal(body.record, false);
    assert.equal(body.visibility, "PERSONAL");
    assert.deepEqual(body.state, state);
    const asked = questionsFor(state);
    const { questions, policy, input } = body.definition;
    assert.deepEqual(Object.keys(questions), ["action", "target"]);
    assert.deepEqual(questions.action, { type: "choice", prompt: asked.action.instructions,
        options: Object.fromEntries(Object.entries(asked.action.criteria).map(([key, text]) => [key, { description: text }])) });
    assert.deepEqual(questions.target, { type: "choice", prompt: asked.target.instructions,
        options: { none: { description: asked.target.criteria.none } }, fallback: "none" });
    // The call's conversations go with the call, beside the declared `none`.
    assert.deepEqual(body.options, { target: { c0: { description: 'Existing conversation "Indian companies" (id one). It is the current conversational focus.' } } });
    assert.deepEqual(policy, { lane: "interactive", escalate_to_model: false, abstain_below: 0 });
    assert.deepEqual(input, { max_chars: EVIDENCE_CHARS });
    // So the definition is one inline decider whatever conversations are open.
    const more = decisionRequest({ ...state, conversations: [...state.conversations, { ...state.conversations[0], id: "two", title: "Other" }] });
    assert.deepEqual(more.definition, body.definition);
    assert.deepEqual(Object.keys(more.options.target), ["c0", "c1"]);
    assert.equal(more.record, false);
    assert.equal("subject" in more, false);
});

test("an event is asked with its confidence bar and a quiet fallback; a lone target is not asked", () => {
    const event = decisionRequest(eventState);
    assert.deepEqual(Object.keys(event.definition.questions), ["delivery"]);
    assert.equal(event.definition.questions.delivery.fallback, "context");
    assert.deepEqual(Object.keys(event.definition.questions.delivery.options), ["speak", "context", "ignore"]);
    assert.deepEqual(event.definition.policy, { lane: "interactive", escalate_to_model: false, abstain_below: 0.65 });
    assert.deepEqual(event.options, {});
    assert.equal(event.record, false);
    // The API needs two options to choose between; with no conversation, `none` is the only target.
    const empty = decisionRequest({ ...state, focusedConversationId: null, conversations: [] });
    assert.deepEqual(Object.keys(empty.definition.questions), ["action"]);
    assert.deepEqual(empty.options, {});
});

test("routing failures dispatch nothing and say so; oversized state is never sent", async () => {
    const failing = backend(() => { throw new Error("503: the provider said something long"); });
    await assert.rejects(classifyCall(state, { client: failing.client, podId: "pod-1" }), { message: UNAVAILABLE });
    const undecided = backend(() => ({ answers: { target: model("c0") }, open: ["action"] }));
    await assert.rejects(classifyCall(state, { client: undecided.client, podId: "pod-1" }), { message: UNAVAILABLE });
    const unsent = backend(() => ({ answers: {} }));
    await assert.rejects(classifyCall({ ...state, utterance: "x".repeat(24_001) }, { client: unsent.client, podId: "pod-1" }), { message: UNAVAILABLE });
    await assert.rejects(classifyCall({ ...state, conversations: Array(25).fill(state.conversations[0]) }, { client: unsent.client, podId: "pod-1" }), { message: UNAVAILABLE });
    assert.equal(unsent.requests.length, 0);
    // Ending the call abandons the request in flight, as a fetch would.
    const ended = new AbortController();
    const hanging = backend((_body, signal) => new Promise((_, reject) => signal?.addEventListener("abort", () => reject(new Error("aborted")))));
    const pending = classifyCall(state, { client: hanging.client, podId: "pod-1" }, ended.signal);
    ended.abort();
    await assert.rejects(pending, { message: UNAVAILABLE });
    assert.equal(hanging.requests[0].signal?.aborted, true);
});

test("a long call's state is fitted newest-first rather than cut from the end", () => {
    const conversations = Array.from({ length: 16 }, (_, i) => ({ ...state.conversations[0], id: `id-${i}`, title: `Conversation ${i}`,
        messages: Array.from({ length: 40 }, (_, m) => ({ role: m % 2 ? "assistant" : "user", text: `${i}:${m} ${"x".repeat(3990)}`, at: "2026-09-17" })) }));
    const event: VoiceEvent = { id: "e", conversationId: "id-3", kind: "completed", text: "Finished the report", speak: false };
    const long: RouterState = { ...eventState, event, focusedConversationId: "id-3", conversations,
        transcript: "oldest speech " + "y".repeat(89_000) + " newest words", podContext: "p".repeat(16_000),
        recentEvents: Array.from({ length: 12 }, (_, i) => ({ ...event, id: `r${i}`, text: `event ${i} ${"z".repeat(1990)}` })) };
    const fitted = evidenceFor(long);
    assert.ok(JSON.stringify(fitted).length <= EVIDENCE_CHARS);
    // What is being decided leads, so nothing past the budget could reach it.
    assert.deepEqual(Object.keys(fitted).slice(0, 3), ["mode", "utterance", "event"]);
    assert.deepEqual(fitted.event, event);
    assert.ok(fitted.transcript.endsWith(" newest words"));
    assert.ok(!fitted.transcript.includes("oldest speech"));
    assert.match(fitted.recentEvents!.at(-1)!.text, /^event 11 /);
    // Every conversation keeps its place, which `c<n>` names, and the focused
    // one keeps its newest messages before any other keeps one.
    assert.deepEqual(fitted.conversations.map(c => c.id), conversations.map(c => c.id));
    assert.match(fitted.conversations[3].messages.at(-1)!.text, /^3:39 /);
    assert.equal(fitted.conversations[0].messages.length, 0);
    assert.deepEqual(decisionRequest(long, "voice-event:e").state, fitted);
    // A short call's state is sent whole.
    assert.deepEqual(evidenceFor(state), state);
});

function fixture(decision: { action: string; conversationId: string | null }, delayed?: () => Promise<void>) {
    const sends: Array<{ id: string; content: string }> = [];
    const events: VoiceEvent[] = [];
    let creates = 0;
    let currentStatus = "WAITING";
    const record = (id: string) => ({ id, title: id, status: currentStatus, updated_at: "2026-09-17", last_run_status: null });
    let reads = 0;
    const frames: unknown[] = [];
    const client: any = { conversations: {
        list: async () => ({ items: [record("one"), record("two")] }),
        get: async (id: string) => record(id),
        create: async () => { creates++; return record("new"); },
        messages: { list: async () => { reads++; return ({ items: [{ id: "m", kind: "TEXT", role: "assistant", text: "Found three companies", created_at: "2026-09-17" }] }); } },
        resumeStream: async () => new ReadableStream({ start(controller) {
            for (const frame of frames) controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(frame)}\n\n`));
            if (frames.length) controller.close();
        } }),
        appendMessage: async (id: string, payload: { content: string }) => { sends.push({ id, content: payload.content }); return { agent_run_id: "run", started_new_run: true }; },
    } };
    const asks: RouteAsk[] = [];
    const router = new ConversationRouter(client, "pod", () => {}, async (state, ask) => {
        asks.push(ask);
        await delayed?.(); return { ...decision, confidence: 0.95, delivery: state.mode === "event" && state.event?.kind === "completed" ? "speak" : "context" } as any;
    });
    router.subscribe(e => events.push(e));
    return { router, client, asks, sends, events, creates: () => creates, status: (next: string) => { currentStatus = next; }, frames, reads: () => reads };
}

test("what's up retrieves a snapshot without creating a conversation or starting work", async () => {
    const f = fixture({ action: "snapshot", conversationId: "two" });
    try {
        await f.router.open("one");
        await f.router.route("u1", "what's up with that?", "User: Tell me about two");
        assert.equal(f.sends.length, 0); assert.equal(f.creates(), 0);
        assert.equal(f.events[0].kind, "snapshot");
        assert.match(f.events[0].text, /Found three companies/);
        assert.equal(f.router.focusedId, "two");
        // Asked in the call's pod, through the call's own client.
        assert.equal(f.asks.length, 1);
        assert.equal(f.asks[0].client, f.client);
        assert.equal(f.asks[0].podId, "pod");
    } finally { f.router.close(); }
});

test("new work creates once, repeated request IDs do not send twice, follow-up uses its target", async () => {
    const f = fixture({ action: "new", conversationId: null });
    try {
        await f.router.open(null); assert.equal(f.creates(), 0);
        await Promise.all([f.router.route("u1", "research this", "User: research this"), f.router.route("u1", "research this", "")]);
        assert.equal(f.creates(), 1); assert.equal(f.sends.length, 1);
        assert.equal(f.sends[0].id, "new");
        assert.equal(f.events[0].kind, "accepted");
    } finally { f.router.close(); }
    const followup = fixture({ action: "existing", conversationId: "two" });
    try {
        await followup.router.open("one"); await followup.router.route("u2", "only September", "User: Update two");
        assert.equal(followup.sends[0].id, "two");
        assert.match(followup.sends[0].content, /only September/);
        assert.match(followup.sends[0].content, /Update two/);
    } finally { followup.router.close(); }
});

test("ending a call during classification prevents late dispatch", async () => {
    let release!: () => void;
    const wait = new Promise<void>(resolve => { release = resolve; });
    const f = fixture({ action: "existing", conversationId: "one" }, () => wait);
    await f.router.open("one");
    const result = f.router.route("u1", "do work", "");
    await Promise.resolve(); f.router.close(); release(); await result;
    assert.equal(f.sends.length, 0);
});

test("utterance fragments and immediate corrections dispatch together; closing drops unfinished speech", () => {
    const sent: string[] = [];
    const buffer = new UtteranceBuffer(text => sent.push(text));
    buffer.push("send it", true); buffer.push(" actually wait", true); buffer.flush();
    assert.deepEqual(sent, ["send it actually wait"]);
    buffer.push("unfinished"); buffer.close(); buffer.flush();
    assert.equal(sent.length, 1);
});


test("stream tokens and completion update state without polling message history", async () => {
    const f = fixture({ action: "existing", conversationId: "one" });
    f.frames.push({ type: "token", data: "Streamed result", kind: "text" }, { type: "completed", data: { status: "COMPLETED" } });
    try {
        await f.router.open("one");
        assert.equal(f.reads(), 1);
        await f.router.route("u1", "research", "User: research");
        await new Promise(resolve => setTimeout(resolve, 20));
        assert.equal(f.reads(), 1);
        assert.equal(f.router.snapshots.get("one")?.partialText, "Streamed result");
        assert.equal(f.events.filter(e => e.kind === "completed").length, 1);
    } finally { f.router.close(); }
});

test("clarification does not end the call and the next utterance is routed", async () => {
    const decision = { action: "clarify", conversationId: null as string | null };
    const f = fixture(decision);
    try {
        await f.router.open("one");
        await f.router.route("u1", "that", "");
        assert.equal(f.events[0].kind, "clarify"); assert.equal(f.sends.length, 0);
        decision.action = "existing"; decision.conversationId = "one";
        await f.router.route("u2", "the research", "User: the research");
        assert.equal(f.sends.length, 1);
    } finally { f.router.close(); }
});

test("a call is offered wherever the voice has its key; routing needs none on this server", async () => {
    const { GET } = await import("../src/app/api/call/config/route.ts");
    const names = ["GEMINI_API_KEY", "OPENAI_API_KEY", "TYPESAFE_API_KEY", "NEXT_PUBLIC_VOICE_PROVIDER"] as const;
    const saved = Object.fromEntries(names.map(name => [name, process.env[name]]));
    const configured = async () => ((await (await GET()).json()) as { configured: boolean }).configured;
    try {
        for (const name of names) delete process.env[name];
        process.env.GEMINI_API_KEY = "test-only";
        assert.equal(await configured(), true);
        process.env.NEXT_PUBLIC_VOICE_PROVIDER = "gpt-live";
        assert.equal(await configured(), false);
        process.env.OPENAI_API_KEY = "test-only";
        assert.equal(await configured(), true);
        delete process.env.OPENAI_API_KEY;
        process.env.TYPESAFE_API_KEY = "test-only";
        assert.equal(await configured(), false);
    } finally {
        for (const name of names) {
            if (saved[name] === undefined) delete process.env[name];
            else process.env[name] = saved[name];
        }
    }
});

test("voice snapshots contain current evidence without replaying old user instructions", async () => {
    const { snapshotText } = await import("../src/call/conversation-router.ts");
    const text = snapshotText({ ...state.conversations[0], messages: [
        { role: "user", text: "old instruction", at: "1" },
        { role: "assistant", text: "old result", at: "2" },
        { role: "user", text: "latest instruction", at: "3" },
        { role: "assistant", text: "current result", at: "4" },
    ] });
    assert.match(text, /current result/);
    assert.doesNotMatch(text, /old instruction|old result|latest instruction|messages/);
});

test("same work with different transcription IDs is dispatched once", async () => {
    const f = fixture({ action: "new", conversationId: null });
    try {
        await f.router.open(null);
        await f.router.route("a", "Research Iran news", "");
        await f.router.route("b", "Research Iran news!", "");
        assert.equal(f.creates(), 1);
        assert.equal(f.sends.length, 1);
        assert.match(f.events.at(-1)!.text, /already accepted/);
        await f.router.route("c", "Research Iran news again", "");
        assert.equal(f.sends.length, 2);
    } finally { f.router.close(); }
});

test("snapshot observation associates subsequent results with the current question", async () => {
    const f = fixture({ action: "snapshot", conversationId: "one" });
    try {
        f.status("RUNNING");
        await f.router.open("one");
        await f.router.route("status-question", "How is it going?", "");
        // Exercise the same publication path used by the live SSE observer.
        await (f.router as any).selectEvent("one", "completed", 0);
        assert.equal(f.events.at(-1)!.responseTo, "status-question");
        assert.equal(f.events.at(-1)!.speak, true);
        assert.equal(f.sends.length, 0);
        // The event's delivery was asked in the call's pod, like every route.
        assert.equal(f.asks.at(-1)!.podId, "pod");
    } finally { f.router.close(); }
});
