import type { CallSocketHandlers, CanvasFrameItem } from "@web/call/call-socket";
import type { CallLink, CallSource } from "./source";

/** A call in sample data: a scripted server instead of the real one, with no
 *  WebView and no microphone. It sends the frames a real call's socket does
 *  (`call-socket.ts`): ready, the voice's and the caller's words a few at a
 *  time, work starting and stopping, one thing on the canvas, so the screen
 *  moves through every state a real call does, mute and end included.
 *
 *  Like the server, it holds each call across connections: one joined again
 *  (after a drop, or by calling again) is ready again, has what it showed
 *  replayed, and carries on from where it was. Support's call drops once and
 *  rejoins; Launch's refuses a request and says why, as a notice, while the
 *  call stays up. */

type Beat =
    | { say: "voice" | "caller"; text: string }
    | { work: boolean }
    | { show: Record<string, unknown> }
    /** A request the Lemma cannot take: the voice says why, and so does the screen. */
    | { notice: string }
    /** The connection goes, as a network does: a close nobody chose. */
    | { drop: true };

interface Script {
    beats: Beat[];
    /** Said when a call that already said everything is joined again. */
    again: string;
}

const SCRIPTS: Record<string, Script> = {
    "sample-support-1140": {
        beats: [
            { say: "voice", text: "I’m here. What should we look at?" },
            { say: "caller", text: "How many tickets are still waiting on us?" },
            { say: "voice", text: "Let me check the queue." },
            { work: true },
            { show: { type: "TABLE", name: "tickets" } },
            { drop: true },
            { work: false },
            { say: "voice", text: "Sorry, we were cut off. Just one is waiting: Orchard Lane, about missing screws. The tickets are on your screen." },
        ],
        again: "I’m back. The tickets are still on your screen.",
    },
    /* Refused as the server refuses a request for a reason saying it again
       will not change: told once, by the voice and on the screen, and the
       call stays up. */
    "sample-launch-1440": {
        beats: [
            { say: "voice", text: "Hi. What do you need?" },
            { say: "caller", text: "What’s left before the launch on Monday?" },
            { notice: "This organization reached its usage limit for the month. New work can’t start until it resets." },
            { say: "voice", text: "I can’t look into that right now. The organization reached its usage limit for the month, so new work can’t start until it resets." },
        ],
        again: "I’m back. The usage limit still holds, so new work can’t start yet.",
    },
};

const FALLBACK: Script = {
    beats: [
        { say: "voice", text: "I’m here. What should we look at?" },
        { say: "caller", text: "How did last week go?" },
        { say: "voice", text: "Let me pull up the report." },
        { work: true },
        { show: { type: "FILE", path: "/reports/week-12.md" } },
        { work: false },
        { say: "voice", text: "Forty of forty-two answered, two waiting on a person. The report is on your screen." },
    ],
    again: "I’m back. The report is still on your screen.",
};

export const scriptOf = (podId: string): Script => SCRIPTS[podId] ?? FALLBACK;

/** How long a word takes to say, near enough. */
const WORD_MS = 150;
/** The pause between one beat and the next. */
const BEAT_MS = 700;

/** What the sample server holds of one call across its connections. */
export interface HeldCall {
    /** The next beat to play. */
    next: number;
    /** What the call has shown, replayed to every new connection. */
    shown: CanvasFrameItem[];
}

/** The server's side of one connection to a sample call, on timers that all
 *  stop together. `held` is the call as the server keeps it. */
export function scriptedCall(podId: string, callId: string, handlers: CallSocketHandlers, held: HeldCall = { next: 0, shown: [] }): CallLink {
    const script = scriptOf(podId);
    const timers = new Set<ReturnType<typeof setTimeout>>();
    let closed = false;
    const at = (ms: number, run: () => void) => {
        const timer = setTimeout(() => {
            timers.delete(timer);
            if (!closed) run();
        }, ms);
        timers.add(timer);
    };
    const stop = () => {
        closed = true;
        for (const timer of timers) clearTimeout(timer);
        timers.clear();
    };
    /* Words one at a time, then the close, which carries the whole line as
       the server's does (`foldTranscript` replaces the open line with it). */
    const say = (start: number, speaker: "voice" | "caller", text: string): number => {
        const words = text.split(" ");
        words.forEach((word, index) => at(start + index * WORD_MS, () => {
            const final = index === words.length - 1;
            handlers.onFrame({ type: "transcript", speaker, text: final ? text : (index ? " " : "") + word, final });
        }));
        return start + words.length * WORD_MS;
    };

    at(700, () => {
        handlers.onFrame({ type: "ready", call_id: callId, engine: "sample", sample_rate: 24000 });
        /* As the server does: what the call showed goes to the new connection. */
        for (const item of held.shown) handlers.onFrame({ type: "canvas.item", item, replay: true });
    });
    let time = 1000;
    if (held.next >= script.beats.length) say(time, "voice", script.again);
    for (let index = held.next; index < script.beats.length; index += 1) {
        const beat = script.beats[index];
        const done = () => { held.next = index + 1; };
        if ("say" in beat) {
            time = say(time, beat.say, beat.text);
            at(time, done);
            time += BEAT_MS;
        } else if ("work" in beat) {
            at(time, () => { done(); handlers.onFrame({ type: "work", running: beat.work }); });
            time += BEAT_MS;
        } else if ("show" in beat) {
            const item: CanvasFrameItem = { conversation_id: callId, tool_call_id: `${callId}-shown-${index}`, tool_name: "display_resource", args: beat.show, result: { ok: true } };
            at(time, () => { done(); held.shown.push(item); handlers.onFrame({ type: "canvas.item", item }); });
            time += BEAT_MS * 2;
        } else if ("notice" in beat) {
            at(time, () => { done(); handlers.onFrame({ type: "notice", text: beat.notice }); });
            time += BEAT_MS;
        } else {
            /* The rest waits for the call to be joined again. */
            at(time, () => {
                done();
                stop();
                handlers.onClose("The call dropped.", true);
            });
            break;
        }
    }

    return {
        sendAudio: () => undefined,
        send: () => undefined,
        close() {
            if (closed) return;
            stop();
            /* As the real socket does on a close of its own: an ordinary end. */
            handlers.onClose(null, false);
        },
    };
}

export function callSample(): CallSource {
    let opened = 0;
    /* The calls this sample server holds, by id, across connections. */
    const calls = new Map<string, HeldCall>();
    return {
        sound: false,
        callable: async () => true,
        /* Held in the chat it was started from, picked up again when it held
           one before; else a chat of its own. */
        async open(podId, _name, conversationId) {
            await new Promise((resolve) => setTimeout(resolve, 300));
            if (conversationId) return conversationId;
            opened += 1;
            return `sample-call-${podId}-${opened}`;
        },
        brief: async () => "",
        connect: async (podId, callId, _start, handlers) => {
            const held = calls.get(callId) ?? { next: 0, shown: [] };
            calls.set(callId, held);
            return scriptedCall(podId, callId, handlers, held);
        },
    };
}
