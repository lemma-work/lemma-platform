import { LoadingIndicator } from "@/ui/loading";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { usePictureInPicture } from "./use-picture-in-picture";
import { CloseIcon, ExternalIcon } from "@/ui/icons";
import { LiveView } from "./live-view";
import { loadLastFrame } from "./last-frame";
import { type LiveState } from "./live";
import { screenSay, type BrowserState, type MachineState } from "./machine";
import { storePreset, storedPreset, type PresetName } from "./presets";
import { useBrowserDetails } from "./queries";
import { ScreenToolbar } from "./screen-toolbar";

/** What a live connection's state means, said in a sentence.
 *
 *  Every one of these is something a person can act on or stop waiting for,
 *  which is the whole reason the close codes are read rather than collapsed
 *  into "disconnected".
 */
function liveNote(state: LiveState): string {
    if (state === "connecting") return "Connecting to the screen…";
    if (state === "no-browser") return "Nothing is open on it yet. This will pick up once something starts.";
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
export function Screen({ state, browser, conversationId, visible, busy, onWake, onOpenTab, fill = false }: {
    state: MachineState;
    browser: BrowserState | undefined;
    /** The screen is the whole view, not a card beside some text.
     *  It connects as soon as there is something to show, because opening
     *  the computer is asking to see it — until somebody stops watching. */
    fill?: boolean;

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

    /* From the click on "Wake it" until the machine answers: a resume takes
       seconds, and a button that does nothing visible for that long gets
       pressed again. */
    const [waking, setWaking] = useState(false);
    const wake = () => { setWaking(true); onWake(); };
    useEffect(() => {
        if (state === "browsing" || state === "awake" || state === "unreachable") setWaking(false);
    }, [state]);

    const [stopped, setStopped] = useState(false);
    useEffect(() => {
        if (!fill || stopped || !visible || say.action !== "show" || watching) return;
        setLive("connecting");
        setWatching(true);
    }, [fill, stopped, visible, say.action, watching]);

    const showing = watching && visible;
    const pip = usePictureInPicture(showing);
    const details = useBrowserDetails(showing);
    const [preset, setPreset] = useState<PresetName>(storedPreset);
    /* Driving is the default on your own machine — nothing to arm first, as
       above — and giving it back is one click when the agent should work
       undisturbed. */
    const [wantControl, setWantControl] = useState(true);
    const [driving, setDriving] = useState(false);
    const [mayDrive, setMayDrive] = useState(true);
    const [agentFromStream, setAgentFromStream] = useState<boolean | null>(null);
    const [sizeNote, setSizeNote] = useState<string | null>(null);
    const agentDriving = agentFromStream ?? details.data?.agent_driving ?? false;
    const [lastFrame] = useState(loadLastFrame);
    const choosePreset = (next: PresetName) => {
        setSizeNote(null);
        setPreset(next);
        storePreset(next);
    };
    const view = (inPip: boolean) => (
        <LiveView
            mode="control"
            conversationId={conversationId}
            setting={details.isPending ? undefined : (details.data?.transport ?? null)}
            density={details.data?.density ?? 1}
            /* The floating window keeps the screen at the preset it has: it
               rescales the picture, and never asks for another size. */
            preset={inPip ? null : preset}
            wantControl={wantControl}
            onState={setLive}
            onControl={(isDriving, may) => { setDriving(isDriving); setMayDrive(may); }}
            onAgentDriving={setAgentFromStream}
            onResizeRefused={setSizeNote}
        />
    );

    return (
        <div className={"screen-panel screen-panel--" + (showing && live === "live" ? "live" : state) + (fill ? " screen-panel--fill" : "")}>
            {showing && fill && !pip.pipWindow && (
                <ScreenToolbar
                    live={live === "live"}
                    driving={driving}
                    mayDrive={mayDrive}
                    agentDriving={agentDriving}
                    watching={details.data?.watching?.length ?? 1}
                    preset={preset}
                    sizeNote={sizeNote}
                    onPreset={choosePreset}
                    onTake={() => { setSizeNote(null); setWantControl(true); }}
                    onGiveBack={() => setWantControl(false)}
                />
            )}
            <div className="screen-glass">
                {/* The page as it was when this machine was last watched,
                    while it wakes: a screen that opens on the page you left
                    reads as starting, one that opens black reads as broken. */}
                {fill && !showing && lastFrame && (state === "asleep" || state === "checking" || state === "awake") && (
                    <img className="screen-last" src={lastFrame} alt="" />
                )}
                {showing ? (
                    <>
                        {pip.pipWindow ? (
                            <button className="screen-action" onClick={pip.close}>Bring it back</button>
                        ) : view(false)}
                        {/* Full size, the panel's own close is the one close:
                            a second × on the picture was a control for the
                            same thing drawn twice. */}
                        {!fill && (
                        <button
                            className="screen-stop"
                            title="Stop watching"
                            aria-label="Stop watching"
                            onClick={() => { setWatching(false); setStopped(true); }}
                        >
                            <CloseIcon size={14} />
                        </button>
                        )}
                    </>
                ) : say.action === "wake" ? (
                    fill ? (
                        <div className="screen-asleep">
                            {waking
                                ? <p role="status">Waking your machine…</p>
                                : <><button className="screen-action" onClick={wake}>Wake it</button><p>{say.note}</p></>}
                        </div>
                    ) : <button className="screen-action" disabled={waking} onClick={wake}>{waking ? "Waking…" : "Wake it"}</button>
                ) : say.action === "show" ? (
                    <button className="screen-action" onClick={() => { setLive("connecting"); setWatching(true); setStopped(false); }}>
                        Show the screen
                    </button>
                ) : fill ? (
                    /* Nothing to show and nothing to press: say so in the middle
                       of the stage rather than leave a black rectangle. */
                    <div className="screen-asleep">
                        <p role="status">{waking ? "Waking your machine…" : say.note}</p>
                    </div>
                ) : null}
            </div>
            {showing && pip.supported && !fill && (
                <button className="computer-inline" onClick={() => pip.pipWindow ? pip.close() : void pip.open()}>
                    <ExternalIcon size={13} /> {pip.pipWindow ? "Bring it back" : "Pop out"}
                </button>
            )}
            {showing && pip.failed && <p className="computer-note" role="alert">Couldn’t open the floating window. You can keep using the browser here.</p>}
            {showing && pip.pipWindow && createPortal(view(true), pip.pipWindow.document.body)}
            {/* Full size, the bar above already says the state; the caption is
                only for what the live connection is doing. */}
            {(!fill || (showing && live !== "live")) && <p className="screen-caption" role="status">
                {showing ? (
                    <>
                        {live !== "live" && <>{liveNote(live)} </>}
                        {live === "live" && <><strong>Live.</strong> You can use this browser too. </>}
                        {(live === "refused" || live === "stale-image" || live === "unsupported") && (
                            <button className="computer-inline" disabled={busy} onClick={onOpenTab}>
                                <ExternalIcon size={13} /> {busy ? <LoadingIndicator inline label="Loading" /> : "Open it in a tab"}
                            </button>
                        )}
                    </>
                ) : (
                    <><strong>{say.headline}.</strong> {say.note}</>
                )}
            </p>}
        </div>
    );
}
