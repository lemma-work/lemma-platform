import { MicIcon, MicOffIcon, EndCallIcon, ExpandIcon } from "@/ui/icons";
import { CallMark } from "./call-mark";
import { statusLine } from "./call-status";
import type { CallStatus } from "./use-call";

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
    notice,
    muted,
    thinking,
    level,
    onMute,
    onEnd,
    onExpand,
}: {
    teammate: string;
    status: CallStatus;
    error: string | null;
    /** Something to know, said once (a request the team cannot take). */
    notice?: string | null;
    muted: boolean;
    /** Someone in the call is working: the server's word, from the runs. */
    thinking?: boolean;
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
                        {statusLine({ error: error ?? notice, muted, thinking, teammate })}
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
