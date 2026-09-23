import { useEffect, useState } from "react";
import { CloseIcon, ExternalIcon } from "@/ui/icons";
import { LiveScreen } from "./live-screen";
import { type LiveState } from "./live";
import { screenSay, type BrowserState, type MachineState } from "./machine";

/** What a live connection's state means, said in a sentence.
 *
 *  Every one of these is something a person can act on or stop waiting for,
 *  which is the whole reason the close codes are read rather than collapsed
 *  into "disconnected".
 */
function liveNote(state: LiveState): string {
    if (state === "connecting") return "Connecting to the screen…";
    if (state === "no-browser") return "Nothing is open on it yet. This will pick up when a teammate starts something.";
    if (state === "signed-out") return "Your session ended. Sign in again to watch.";
    if (state === "unsupported") return "This kind of machine has no screen to show.";
    if (state === "stale-image") return "This machine is running an image too old to show its screen.";
    /* The allowlist, in the one sentence that tells somebody which knob to
       turn. The socket checks the handshake `Origin` against `frontend_url`,
       `api_url` and `auth_frontend_url`, and this app is none of the three, so
       it is closed 4403 before the session is even read. That is deliberate on
       the API's side — a cookie-bearing socket any page could open would let
       any page watch this screen — and it is a deployment's list rather than
       anything this app can talk its way past. */
    if (state === "refused") return "This app is not on the API's allowlist for the screen, so it cannot carry the picture.";
    return "The connection dropped. Trying again…";
}

/** The computer's screen.
 *
 *  A computer has one, and showing it is most of what makes this view read as
 *  a machine rather than as another file browser. It is dark in every theme
 *  for the same reason a real one is: an idle display is not a sheet of paper.
 *
 *  The glass holds the picture and nothing else. What the machine is doing is
 *  said underneath, where a caption belongs — it was written across the glass
 *  once, and anything drawn behind it fought the words.
 *
 *  **Drivable, with nothing to arm first.** This was view-only, on the reasoning
 *  that a stray keystroke could land in a browser mid-run. The relay had a
 *  driving lease for exactly that, and it was taken out again: it never covered
 *  the case it was written for and only ever cost the case it did reach. There
 *  is one browser per sandbox now, so two parties acting at once costs the
 *  teammate a retry — which is cheaper than a person watching a cookie banner
 *  they cannot dismiss on a machine that is theirs. A watch/drive toggle would
 *  only move that cost onto somebody who forgot to press it.
 *
 *  Connecting is always a click. Attaching starts a browser if none is running
 *  and holds the sandbox awake for as long as the socket is open, so a screen
 *  that connected on render would run a machine for anybody who left the tab
 *  open. It disconnects the moment this pane stops being the one on screen.
 */
export function Screen({ state, browser, conversationId, visible, busy, onWake, onOpenTab }: {
    state: MachineState;
    browser: BrowserState | undefined;

    conversationId: string | null;
    visible: boolean;
    busy: boolean;
    onWake: () => void;
    /** The same picture, in a tab. Kept for the case the pane cannot carry it:
     *  a top-level page is not a framed one, so the proxy's `frame-ancestors`
     *  has nothing to say about it and it works where the socket does not. */
    onOpenTab: () => void;
}) {
    const say = screenSay(state, browser);
    const [watching, setWatching] = useState(false);
    const [live, setLive] = useState<LiveState>("connecting");

    /* Stop watching the moment this pane is not the one being looked at, or
       the machine goes back to sleep under it. The socket is what keeps a
       sandbox running, so leaving it open behind another tab is somebody's
       compute spent on nothing. */
    useEffect(() => {
        if (!visible || state === "asleep" || state === "checking") setWatching(false);
    }, [visible, state]);

    const showing = watching && visible;

    return (
        <div className={"screen-panel screen-panel--" + (showing && live === "live" ? "live" : state)}>
            <div className="screen-glass">
                {showing ? (
                    <>
                        <LiveScreen mode="control" conversationId={conversationId} onState={setLive} />
                        <button
                            className="screen-stop"
                            title="Stop watching"
                            aria-label="Stop watching"
                            onClick={() => setWatching(false)}
                        >
                            <CloseIcon size={14} />
                        </button>
                    </>
                ) : say.action === "wake" ? (
                    <button className="screen-action" onClick={onWake}>Wake it</button>
                ) : say.action === "show" ? (
                    <button className="screen-action" onClick={() => { setLive("connecting"); setWatching(true); }}>
                        Show the screen
                    </button>
                ) : null}
            </div>
            <p className="screen-caption" role="status">
                {showing ? (
                    <>
                        {live !== "live" && <>{liveNote(live)} </>}
                        {live === "live" && <><strong>Live.</strong> This is the teammate&rsquo;s browser, and you can use it. </>}
                        {(live === "refused" || live === "stale-image" || live === "unsupported") && (
                            <button className="computer-inline" disabled={busy} onClick={onOpenTab}>
                                <ExternalIcon size={13} /> {busy ? "Opening…" : "Open it in a tab"}
                            </button>
                        )}
                    </>
                ) : (
                    <><strong>{say.headline}.</strong> {say.note}</>
                )}
            </p>
        </div>
    );
}
