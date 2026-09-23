import test from "node:test";
import assert from "node:assert/strict";
import { decisionFrom, questionsFor, routeWithJev } from "../src/call/jev-router.ts";
import { ConversationRouter } from "../src/call/conversation-router.ts";
import { UtteranceBuffer } from "../src/call/utterance-buffer.ts";
import type { RouterState, VoiceEvent } from "../src/call/routing.ts";

const state: RouterState = { mode: "utterance", utterance: "What's up with that?", transcript: "User: Research Indian companies", podContext: "Research pod",
    focusedConversationId: "one", conversations: [{ id: "one", title: "Indian companies", status: "RUNNING", updatedAt: "2026-09-17", fetchedAt: "2026-09-17",
        lastRunStatus: "RUNNING", error: null, messages: [], plan: [], resources: [], needsInput: false, openQuestions: [] }] };
const answer = (choice: string, confidence = 0.95) => ({ choice, confidence, probabilities: { [choice]: confidence } });

test("routing keeps selected choices regardless of confidence; missing targets do not dispatch", () => {
    assert.equal(decisionFrom(state, { action: answer("snapshot"), target: answer("c0") }).conversationId, "one");
    assert.equal(decisionFrom(state, { action: answer("existing"), target: answer("c0", 0.3) }).action, "existing");
    assert.equal(decisionFrom(state, { action: answer("snapshot"), target: answer("none") }).action, "clarify");
    assert.throws(() => decisionFrom(state, { action: answer("existing"), target: answer("invented-id") }));
    assert.equal(decisionFrom(state, { action: answer("new"), target: answer("none") }).action, "new");
});

test("event selection has no execution route and uncertain events stay quiet", () => {
    const eventState = { ...state, mode: "event" as const, event: { id: "event", conversationId: "one", kind: "progress" as const, text: "Working", speak: false } };
    assert.deepEqual(Object.keys(questionsFor(eventState)), ["delivery"]);
    assert.equal(decisionFrom(eventState, { delivery: answer("speak", 0.3) }).delivery, "context");
});

test("Jev receives rich state and typed questions in the official wire format", async () => {
    const result = await routeWithJev(state, { apiKey: "test-only", fetch: async (url, init) => {
        assert.equal(url, "https://api.typesafe.ai/v1/systemone");
        const body = JSON.parse(String(init?.body));
        assert.equal(body.model, "jev-latest");
        assert.deepEqual(body.state, state);
        assert.equal(body.questions.action.type, "choice");
        assert.equal(body.questions.target.criteria.c0.id, "one");
        return Response.json({ answers: { action: answer("snapshot"), target: answer("c0") } });
    } });
    assert.equal(result.action, "snapshot");
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
    const router = new ConversationRouter(client, "pod", () => {}, async (state) => {
        await delayed?.(); return { ...decision, confidence: 0.95, delivery: state.mode === "event" && state.event?.kind === "completed" ? "speak" : "context" } as any;
    });
    router.subscribe(e => events.push(e));
    return { router, sends, events, creates: () => creates, status: (next: string) => { currentStatus = next; }, frames, reads: () => reads };
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

test("HTTP routing rejects cross-origin requests and returns only the normalized decision", async context => {
    const { POST } = await import("../src/app/api/call/route/route.ts");
    const previous = process.env.TYPESAFE_API_KEY;
    process.env.TYPESAFE_API_KEY = "test-only";
    let calls = 0;
    context.mock.method(globalThis, "fetch", async () => {
        calls++;
        return Response.json({ answers: { action: answer("snapshot"), target: answer("c0") } });
    });
    try {
        const forbidden = await POST(new Request("https://pod.example/api/call/route", { method: "POST", headers: { origin: "https://elsewhere.example" }, body: JSON.stringify(state) }));
        assert.equal(forbidden.status, 403); assert.equal(calls, 0);
        const response = await POST(new Request("https://pod.example/api/call/route", { method: "POST", headers: { origin: "https://pod.example" }, body: JSON.stringify(state) }));
        assert.equal(response.status, 200);
        const body = await response.json();
        assert.equal(body.action, "snapshot"); assert.equal(body.conversationId, "one");
        assert.doesNotMatch(JSON.stringify(body), /test-only/); assert.equal(calls, 1);
        const local = await POST(new Request("http://0.0.0.0:3000/api/call/route", {
            method: "POST", headers: { host: "localhost:3000", origin: "http://localhost:3000" }, body: JSON.stringify(state),
        }));
        assert.equal(local.status, 200); assert.equal(calls, 2);
        const wrongPort = await POST(new Request("http://0.0.0.0:3000/api/call/route", {
            method: "POST", headers: { host: "localhost:3000", origin: "http://localhost:4000" }, body: JSON.stringify(state),
        }));
        assert.equal(wrongPort.status, 403); assert.equal(calls, 2);
    } finally {
        if (previous === undefined) delete process.env.TYPESAFE_API_KEY;
        else process.env.TYPESAFE_API_KEY = previous;
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
    } finally { f.router.close(); }
});
