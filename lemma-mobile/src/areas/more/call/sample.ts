import { samplePulse } from "../../../data/sample";
import { NEW_CHAT } from "../../../data/routes";
import type { RawMessage } from "@web/thread/turns";
import type { FromPage, ToPage } from "./model";
import type { Profile } from "./profile";
import type { CallSession, CallSource, EngineLink } from "./source";
import { newId } from "@web/call/ids";
import { podBrief } from "@web/call/pod-brief";
import type { VoiceEvent } from "@web/call/routing";

/** A call in sample data: a scripted voice instead of the page, and a
 *  scripted Lemma instead of the router, with no WebView and no microphone.
 *  The call itself (`machine.ts`) is the real one: it hears the voice, sends
 *  what is said to be routed, and hands the answer back at a pause, so the
 *  screen moves through every state a real call does. Connecting, live, a
 *  few captions both ways, the status line through working and done, one
 *  thing on screen, mute and end. */

interface Script {
    /** The chat the work lands in when the call was not about one. */
    title: string;
    greeting: string;
    ask: string;
    ack: string;
    steps: [string, string];
    shown: Record<string, unknown>;
    written: string;
    answer: string;
}

const SCRIPTS: Record<string, Script> = {
    "sample-support-1140": {
        title: "Tickets still waiting",
        greeting: "I’m here. What should we look at?",
        ask: "How many tickets are still waiting on us?",
        ack: "Let me check the queue.",
        steps: ["Reading the tickets table", "Counting what is waiting"],
        shown: { type: "TABLE", name: "tickets" },
        written: "One ticket is waiting on us: T-1038 from Orchard Lane, about missing screws. The other three are answered or refunded.",
        answer: "Just one. Orchard Lane, about missing screws. The tickets are on your screen.",
    },
    "sample-launch-1440": {
        title: "Before Monday",
        greeting: "Hi. What do you need?",
        ask: "What’s left before the launch on Monday?",
        ack: "One moment, I’ll look at the list.",
        steps: ["Reading the task list", "Picking out what is not done"],
        shown: { type: "TABLE", name: "tasks" },
        written: "Three things are left: photographing the spring range (Sam, Tuesday), the catalogue blurbs (Dev, Wednesday) and booking the courier (Ada, Thursday).",
        answer: "Three things: the photographs, the catalogue blurbs and the courier. The list is on your screen.",
    },
};

const FALLBACK: Script = {
    title: "Last week",
    greeting: "I’m here. What should we look at?",
    ask: "How did last week go?",
    ack: "Let me pull up the report.",
    steps: ["Reading last week’s report", "Checking what is still open"],
    shown: { type: "FILE", path: "/reports/week-12.md" },
    written: "40 of 42 questions were answered last week, and 2 are waiting on a person. The first reply took 6 minutes on average.",
    answer: "Forty of forty-two answered, two waiting on a person. The report is on your screen.",
};

const scriptOf = (podId: string) => SCRIPTS[podId] ?? FALLBACK;

const pause = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** Timers that all stop together. */
function timers() {
    const held = new Set<ReturnType<typeof setTimeout>>();
    return {
        later(ms: number, run: () => void) {
            const timer = setTimeout(() => {
                held.delete(timer);
                run();
            }, ms);
            held.add(timer);
        },
        stop() {
            for (const timer of held) clearTimeout(timer);
            held.clear();
        },
    };
}

/** How long a word takes to say, near enough. */
const WORD_MS = 150;

/** The voice, as the page would report it: words a fragment at a time, and
 *  while it speaks, `playing`, `spoke` and a level for the ring. */
export function scriptedVoice(podId: string): EngineLink {
    const script = scriptOf(podId);
    const clock = timers();
    let hear: ((message: FromPage) => void) | null = null;
    let muted = false;
    let asked = false;
    let held = false;
    let answered = false;

    const tell = (message: FromPage) => hear?.(message);

    const say = (speaker: "us" | "them", text: string, then?: () => void) => {
        const words = text.split(" ");
        if (speaker === "us") tell({ type: "playing", on: true });
        words.forEach((word, index) => {
            clock.later(index * WORD_MS, () => {
                tell({ type: "transcript", speaker, text: (index ? " " : "") + word, final: index === words.length - 1 });
                if (speaker === "us") {
                    tell({ type: "spoke" });
                    tell({ type: "level", value: 0.35 + ((index * 37) % 50) / 100 });
                }
            });
        });
        clock.later(words.length * WORD_MS + 200, () => {
            if (speaker === "us") {
                tell({ type: "level", value: 0 });
                tell({ type: "playing", on: false });
                tell({ type: "turn-complete" });
            }
            then?.();
        });
    };

    /* The person asks once. Muted, they are not heard: the question waits
       until the microphone is back. */
    const ask = () => {
        if (asked) return;
        if (muted) {
            held = true;
            return;
        }
        asked = true;
        say("them", script.ask, () => clock.later(500, () => say("us", script.ack)));
    };

    return {
        open(listener) {
            hear = listener;
            clock.later(150, () => tell({ type: "ready" }));
        },
        post(message: ToPage) {
            switch (message.type) {
                case "start":
                    clock.later(1100, () => tell({ type: "open" }));
                    return;
                case "listen":
                    tell({ type: "listening" });
                    clock.later(700, () => say("us", script.greeting, () => clock.later(900, ask)));
                    return;
                case "reply":
                    if (answered) return;
                    answered = true;
                    say("us", script.answer);
                    return;
                case "mute":
                    muted = message.on;
                    if (!muted && held) {
                        held = false;
                        clock.later(800, ask);
                    }
                    return;
                case "end":
                    clock.stop();
                    return;
                default:
                    return;
            }
        },
        close() {
            clock.stop();
            hear = null;
        },
    };
}

/** The Lemma behind a sample call: it takes the one request, writes a plan,
 *  works through it, puts one thing on the call's screen and answers. */
export class SampleCallSession implements CallSession {
    focusedId: string | null = null;
    readonly messages = new Map<string, RawMessage[]>();
    readonly snapshots = new Map<string, { title: string }>();
    private running = false;
    private active = false;
    private taken = false;
    private sequence = 0;
    private readonly listeners = new Set<(event: VoiceEvent) => void>();
    private readonly clock = timers();
    private readonly script: Script;

    constructor(private readonly podId: string, private readonly changed: () => void) {
        this.script = scriptOf(podId);
    }

    get isRunning(): boolean {
        return this.running;
    }

    async open(selectedId: string | null): Promise<string | null> {
        await pause(300);
        this.active = true;
        this.taken = false;
        const id = selectedId && selectedId !== NEW_CHAT ? selectedId : null;
        this.focusedId = id;
        if (id) {
            const title = samplePulse(Date.now()).find((pulse) => pulse.lemma.id === this.podId)?.conversations.find((one) => one.id === id)?.title;
            this.messages.set(id, []);
            this.snapshots.set(id, { title: title ?? "This chat" });
        }
        this.changed();
        return id;
    }

    close = () => {
        this.active = false;
        this.clock.stop();
        this.focusedId = null;
        this.messages.clear();
        this.snapshots.clear();
        this.running = false;
        this.changed();
    };

    subscribe = (listener: (event: VoiceEvent) => void) => {
        this.listeners.add(listener);
        return () => { this.listeners.delete(listener); };
    };

    setContext = () => undefined;
    observeTranscript = () => undefined;

    private emit(event: VoiceEvent): void {
        if (!this.active) return;
        for (const listener of this.listeners) listener(event);
    }

    private push(target: string, message: RawMessage): void {
        this.sequence += 1;
        const list = this.messages.get(target) ?? [];
        this.messages.set(target, [...list, { ...message, sequence: this.sequence, created_at: new Date().toISOString() }]);
        this.changed();
    }

    private plan(target: string, n: number, statuses: [string, string]): void {
        this.push(target, {
            id: `plan-${n}`,
            role: "assistant",
            kind: "TOOL_CALL",
            tool_name: "update_plan",
            tool_call_id: `call-plan-${n}`,
            tool_args: { plan: this.script.steps.map((step, index) => ({ step, status: statuses[index] })) },
        });
    }

    route = async (id: string, text: string) => {
        /* One piece of work per sample call; anything said after it is
           conversation, which the voice answers on its own. */
        if (!this.active || this.taken) return;
        this.taken = true;
        await pause(400);
        if (!this.active) return;
        let target = this.focusedId;
        if (!target) {
            target = `call-${this.podId}`;
            this.messages.set(target, []);
            this.snapshots.set(target, { title: this.script.title });
        }
        this.focusedId = target;
        const title = this.snapshots.get(target)?.title ?? this.script.title;
        this.push(target, { id: newId(), role: "user", kind: "TEXT", content: text });
        this.running = true;
        this.changed();
        this.emit({ id: newId(), conversationId: target, kind: "accepted", speak: false, text: `Work on ${title} accepted. Started work. User request: ${text}` });

        const at = target;
        this.clock.later(700, () => this.plan(at, 1, ["in_progress", "pending"]));
        this.clock.later(2200, () => {
            this.push(at, { id: "shown", role: "assistant", kind: "TOOL_CALL", tool_name: "display_resource", tool_call_id: "call-shown", tool_args: this.script.shown });
            this.push(at, { id: "shown-r", role: "tool", kind: "TOOL_RETURN", tool_name: "display_resource", tool_call_id: "call-shown", tool_result: { ok: true } });
        });
        this.clock.later(3600, () => this.plan(at, 2, ["completed", "in_progress"]));
        this.clock.later(5000, () => {
            this.plan(at, 3, ["completed", "completed"]);
            this.push(at, { id: newId(), role: "assistant", kind: "TEXT", content: this.script.written });
            this.running = false;
            this.changed();
            this.emit({
                id: newId(),
                conversationId: at,
                kind: "completed",
                speak: true,
                responseTo: id,
                text: JSON.stringify({ task: title, status: "IDLE", currentResult: this.script.written }),
            });
        });
    };
}

/** The profile a sample Lemma's voice is briefed from. */
function sampleProfile(podId: string, name: string): Profile {
    return {
        podId,
        name,
        iconUrl: null,
        headline: "",
        joined: "",
        about: `${name} is a sample Lemma in Acme. Nothing it says is live.`,
        skills: [{ id: "POD", label: "Works with its tables and files", blurb: "" }],
        permits: [],
        commitments: [],
        projects: [],
        counts: { tables: 2, functions: 0, workflows: 1 },
    };
}

export function callSample(): CallSource {
    return {
        async readiness() {
            await pause(250);
            return { configured: true, provider: "gemini" };
        },
        async brief(podId, name) {
            return podBrief(sampleProfile(podId, name), name);
        },
        session(podId, changed) {
            return new SampleCallSession(podId, changed);
        },
        engine: scriptedVoice,
    };
}
