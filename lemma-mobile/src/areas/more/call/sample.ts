import type { CallSocketHandlers } from "@web/call/call-socket";
import type { CallLink, CallSource } from "./source";

/** A call in sample data: a scripted server instead of the real one, with no
 *  WebView and no microphone. It sends the frames a real call's socket does
 *  (`call-socket.ts`): ready, the voice's and the caller's words a few at a
 *  time, work starting and stopping, one thing on the canvas, so the screen
 *  moves through every state a real call does, mute and end included. */

interface Script {
    greeting: string;
    ask: string;
    ack: string;
    shown: Record<string, unknown>;
    answer: string;
}

const SCRIPTS: Record<string, Script> = {
    "sample-support-1140": {
        greeting: "I’m here. What should we look at?",
        ask: "How many tickets are still waiting on us?",
        ack: "Let me check the queue.",
        shown: { type: "TABLE", name: "tickets" },
        answer: "Just one. Orchard Lane, about missing screws. The tickets are on your screen.",
    },
    "sample-launch-1440": {
        greeting: "Hi. What do you need?",
        ask: "What’s left before the launch on Monday?",
        ack: "One moment, I’ll look at the list.",
        shown: { type: "TABLE", name: "tasks" },
        answer: "Three things: the photographs, the catalogue blurbs and the courier. The list is on your screen.",
    },
};

const FALLBACK: Script = {
    greeting: "I’m here. What should we look at?",
    ask: "How did last week go?",
    ack: "Let me pull up the report.",
    shown: { type: "FILE", path: "/reports/week-12.md" },
    answer: "Forty of forty-two answered, two waiting on a person. The report is on your screen.",
};

export const scriptOf = (podId: string): Script => SCRIPTS[podId] ?? FALLBACK;

/** How long a word takes to say, near enough. */
const WORD_MS = 150;

/** The server's side of one sample call, on timers that all stop together. */
export function scriptedCall(podId: string, callId: string, handlers: CallSocketHandlers): CallLink {
    const script = scriptOf(podId);
    const held = new Set<ReturnType<typeof setTimeout>>();
    let closed = false;
    const at = (ms: number, run: () => void) => {
        const timer = setTimeout(() => {
            held.delete(timer);
            if (!closed) run();
        }, ms);
        held.add(timer);
    };
    /* Words a few at a time, the last one final; when the line ends. */
    const say = (start: number, speaker: "voice" | "caller", text: string): number => {
        const words = text.split(" ");
        words.forEach((word, index) => at(start + index * WORD_MS, () => handlers.onFrame({ type: "transcript", speaker, text: (index ? " " : "") + word, final: index === words.length - 1 })));
        return start + words.length * WORD_MS;
    };

    at(700, () => handlers.onFrame({ type: "ready", call_id: callId, engine: "sample", sample_rate: 24000 }));
    let time = say(1000, "voice", script.greeting);
    time = say(time + 1200, "caller", script.ask);
    time = say(time + 600, "voice", script.ack);
    at(time, () => handlers.onFrame({ type: "work", running: true }));
    at(time + 1800, () => handlers.onFrame({
        type: "canvas.item",
        item: { conversation_id: callId, tool_call_id: `${callId}-shown`, tool_name: "display_resource", args: script.shown, result: { ok: true } },
    }));
    at(time + 2400, () => handlers.onFrame({ type: "work", running: false }));
    say(time + 2600, "voice", script.answer);

    return {
        sendAudio: () => undefined,
        send: () => undefined,
        close() {
            if (closed) return;
            closed = true;
            for (const timer of held) clearTimeout(timer);
            held.clear();
            /* As the real socket does on a close of its own: an ordinary end. */
            handlers.onClose(null);
        },
    };
}

export function callSample(): CallSource {
    let opened = 0;
    return {
        sound: false,
        callable: async () => true,
        async open(podId) {
            await new Promise((resolve) => setTimeout(resolve, 300));
            opened += 1;
            return `sample-call-${podId}-${opened}`;
        },
        brief: async () => "",
        connect: async (podId, callId, _start, handlers) => scriptedCall(podId, callId, handlers),
    };
}
