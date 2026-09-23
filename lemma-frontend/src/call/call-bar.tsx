import { MicIcon, MicOffIcon, EndCallIcon, ExpandIcon } from "@/ui/icons";
import { CallMark } from "./call-mark";
import { statusLine } from "./call-status";
import type { CallStatus } from "./use-call";
import type { PlanStepState } from "@/thread/turns";

/** The call, minimized.
 *
 *  The other half of `CallScreen` — what a huddle leaves behind when you go
 *  back to the pod. Deliberately not a replacement for the composer: taking
 *  the composer makes the call the whole window, so the only two moves are
 *  end it or keep looking at it. The call does not pause because you looked
 *  away, and this bar is both the proof of that and the way back to it.
 *
 *  It sits at pod level rather than inside the conversation, which is what
 *  lets a call survive opening a new conversation or switching pods. */
export function CallBar({
    teammate,
    status,
    error,
    muted,
    thinking,
    plan,
    level,
    onMute,
    onEnd,
    onExpand,
}: {
    teammate: string;
    status: CallStatus;
    error: string | null;
    muted: boolean;
    /** Still reasoning after it stopped talking — Gemini 3.8 Live Extended
     *  Thinking's own signal, not a guess made from the audio going quiet. */
    thinking?: boolean;
    /** The teammate's own plan, so the bar says the same thing the screen
     *  does rather than keeping a second opinion. */
    plan?: PlanStepState[];
    level: number;
    onMute: () => void;
    onEnd: () => void;
    onExpand: () => void;
}) {
    return (
        <div className="callbar-dock">
            <div className="callbar">
                <CallMark teammate={teammate} level={level} size="bar" />

                <button className="callbar__what" onClick={onExpand} title="Back to the call">
                    <span className="callbar__who">
                        {status === "connecting" ? "Connecting to " + teammate + "…" : "On a call with " + teammate}
                    </span>
                    <span className="callbar__hint">
                        {statusLine({ error, muted, plan, thinking, teammate })}
                    </span>
                </button>

                <button className="callbar__open" onClick={onExpand} aria-label="Back to the call">
                    <ExpandIcon size={18} />
                </button>
                <button className="callbar__mute" onClick={onMute} aria-pressed={muted}>
                    {muted ? <MicOffIcon size={18} /> : <MicIcon size={18} />}{muted ? "Unmute" : "Mute"}
                </button>
                <button className="callbar__end" onClick={onEnd}>
                    <EndCallIcon size={18} />End
                </button>
            </div>
        </div>
    );
}
