import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { ExternalIcon, PlusIcon, CloseIcon } from "@/ui/icons";
import { appThemeMessage, onAppearanceChange, widgetThemeMessage, widgetThemeStyle } from "./widget-theme";
import { registerFrame } from "./compose-bridge";
import { frameState } from "./frame-state";

/** A widget, drawn as the thing it is.
 *
 *  Two problems, and they were the same problem. It carried a toolbar with its
 *  own name in it, a hint line and a border, all around content whose whole
 *  purpose is to be looked at — while inside the frame it knew nothing about
 *  the page it was in and rendered white in a dark one.
 *
 *  So the frame is bare and the controls come to the pointer, and the theme is
 *  handed across the boundary: posted for anything running the SDK, which
 *  listens for it already, and written straight into the document for inline
 *  content that has no SDK to listen with. */
/** Nothing is taller than this, and nothing is shorter. */
const HEIGHT_MIN = 64;
const HEIGHT_MAX = 2400;
/** How long to wait for a height before concluding none is coming. Generous on
 *  purpose: the cost of waiting is a moment of "Loading…", and the cost of
 *  giving up early is standing a 480px box up and then jumping to the real
 *  height when the answer arrives a beat later — which is the jolt this whole
 *  pass is about removing. A widget here fetches pod data before it can know
 *  its own size, so slow is normal. */
const REPORT_GRACE_MS = 2500;
/** How long a frame gets to load at all before this says so. The grace above
 *  is for content that loaded and stayed quiet about its height; this is for
 *  content that never loaded, which is a different thing and needs its own
 *  end: the wait for a height only begins once `load` has fired, so without
 *  this a frame whose navigation was refused or turned into a download sits on
 *  "Loading…" for as long as anybody leaves it there. Generous, because a slow
 *  page is not a failed one. */
const LOAD_DEADLINE_MS = 12_000;
/** The cap on an unexpanded widget: most of the viewport, never less than this. */
const CEILING_MIN = 480;
const CEILING_SHARE = 0.85;

/** Markup this app did not write, wrapped so it can live in a frame here.
 *
 *  Three things go in with it, and the file view on the stage needs the same
 *  three: the theme as a stylesheet for the first paint, a listener so a theme
 *  changed later reaches it without reloading the frame and throwing away
 *  whatever it was showing, and `window.lemma.compose` so inline content can
 *  ask the pod something without an SDK to call.
 *
 *  Exported because there are two frames, not one. The stage drew its own with
 *  the raw markup and none of this, so a generated page sat glowing white on a
 *  dark shell — the exact failure `widget-theme.ts` exists to fix, in the one
 *  place the fix had not reached. */
export function framedDocument(html: string | undefined, id: string): string | undefined {
    if (html === undefined) return undefined;
    const bridge = `<script>(()=>{let last=0;const send=()=>{const h=Math.ceil(Math.max(document.body?.scrollHeight||0,document.documentElement.scrollHeight));if(h!==last){last=h;parent.postMessage({type:'lemma:preview-height',id:${JSON.stringify(id)},height:h},'*')}};new ResizeObserver(send).observe(document.documentElement);addEventListener('load',send);send();addEventListener('message',e=>{if(e.source!==parent)return;const d=e.data;if(!d||d.type!=='lemma-widget-theme'||!d.tokens)return;const r=document.documentElement;for(const k in d.tokens){if(k.indexOf('--lemma-widget-')===0)r.style.setProperty(k,d.tokens[k])}r.dataset.lemmaTheme=d.theme;r.style.colorScheme=d.theme});window.lemma={compose:(text,options)=>{parent.postMessage({type:'lemma-compose',id:String(Date.now())+Math.random(),text:String(text),newConversation:!!(options&&options.newConversation)},'*')}}})()<\/script>`;
    return `<style>${widgetThemeStyle()}</style>` + html + bridge;
}

export function EmbedPreview({ title, html, src, sandbox = "allow-scripts", full = false, onOpen }: {
    title: string; html?: string; src?: string; sandbox?: string; full?: boolean; onOpen?: () => void;
}) {
    const frame = useRef<HTMLIFrameElement>(null);
    const id = useId();
    const [expanded, setExpanded] = useState(full);
    /* Null until the content says how tall it is. That distinction is the whole
       fix: a number we have not been told is not a height, and standing a fixed
       box up while we wait is what put a white slab under every short widget. */
    const [reported, setReported] = useState<number | null>(null);
    const [ceiling, setCeiling] = useState(CEILING_MIN);
    /* A frame that has not loaded is showing nothing, and a frame that has
       loaded but not yet said how tall it is, is showing its content at the
       wrong size. Both looked the same before: raw content clipped to 180px
       that then jumped. It stays hidden until it is neither. */
    const [loaded, setLoaded] = useState(false);
    const [unreported, setUnreported] = useState(false);
    /** Long enough with no `load` at all that saying "Loading…" is a lie. */
    const [stalled, setStalled] = useState(false);

    /* A ceiling, not a height. It exists only to stop one widget swallowing the
       transcript — tall enough that anything normally shaped renders whole,
       short enough that the next message is still reachable by scrolling. */
    useEffect(() => {
        const measure = () => setCeiling(Math.max(CEILING_MIN, Math.round(window.innerHeight * CEILING_SHARE)));
        measure();
        window.addEventListener("resize", measure);
        return () => window.removeEventListener("resize", measure);
    }, []);

    /* srcDoc has an opaque origin, so the theme goes in with the markup. A
       minted widget is served from its own origin and is addressed there
       rather than at "*", which would hand the pod's tokens to whatever
       happened to be loaded in the frame. */
    const target = useMemo(() => { try { return src ? new URL(src).origin : null; } catch { return null; } }, [src]);

    /* The stylesheet is the first paint and the listener is every one after it.
       Rebuilding the document on a theme change would reload the frame and throw
       away whatever the widget was showing, so inline content gets the same
       contract the SDK implements: apply `--lemma-app-*` off a message from the
       parent. Content built against the browser bundle already does this; this
       is for content that was not.

       `window.lemma.compose` rides along for the same reason. Inline content has
       no SDK to call, and whether a widget can ask the pod something should not
       depend on which of the two ways it happened to be served. */
    const document_ = useMemo(() => framedDocument(html, id), [html, id, expanded]);

    /* Both vocabularies go out. A widget served by the platform listens for
       `lemma-widget-theme`; anything built on the browser SDK has a listener for
       `lemma-app-theme` registered the moment it is framed. Each side ignores
       the message it does not recognise, and sending one and not the other is
       how the first pass at this themed nothing at all. */
    const loadedRef = useRef(false);
    const sendTheme = useCallback(() => {
        const view = frame.current?.contentWindow;
        if (!view) return;
        /* A frame with a `src` has not navigated to it on first render: its
           contentWindow is still the initial about:blank, which carries this
           page's origin rather than the widget's. Addressing the widget's
           origin then cannot land — the browser refuses it and logs a security
           error, once per widget on screen. `onLoad` sends the first one, and
           the frame is real by then. */
        if (target && !loadedRef.current) return;
        const where = target ?? "*";
        try {
            view.postMessage(widgetThemeMessage(), where);
            view.postMessage(appThemeMessage(), where);
        } catch { /* frame not ready yet */ }
    }, [target]);

    /* Re-sent on every appearance change, because a widget that only hears the
       first one is wrong the moment somebody switches theme — or their machine
       does it for them at sunset. */
    useEffect(() => {
        sendTheme();
        return onAppearanceChange(sendTheme);
    }, [sendTheme]);

    /* Vouching for this frame, so the app will take a compose request from it.
       Done on load rather than on mount: `contentWindow` is replaced on every
       navigation, and the window that asks has to be the window we registered.
       The previous registration is dropped first, so a frame that navigates
       away does not leave a window the pod still trusts. */
    const unregister = useRef<() => void>(() => undefined);
    const registerThisFrame = useCallback(() => {
        unregister.current();
        unregister.current = registerFrame(frame.current?.contentWindow);
    }, []);
    useEffect(() => () => unregister.current(), []);

    /* Registered unconditionally, and specifically not gated on `html`.
       `html === undefined` is every widget the platform serves, so gating on
       it leaves the one kind that reports its own height as the one kind
       nothing is listening to. */
    useEffect(() => {
        const resize = (event: MessageEvent) => {
            if (event.source !== frame.current?.contentWindow) return;
            const data = event.data as { type?: string; id?: string; height?: unknown } | null;
            if (!data) return;
            /* Two reporters, one meaning. A widget the platform serves posts
               `lemma-widget-height` of its own accord; inline content posts the
               bridge message this file writes into it. Listening for only the
               second is why a minted widget was pinned at a hard 480px: it was
               telling us its height the whole time and nothing was listening. */
            const mine = data.type === "lemma:preview-height" && data.id === id;
            const theirs = data.type === "lemma-widget-height";
            if (!mine && !theirs) return;
            const value = typeof data.height === "number" ? data.height : Number(data.height);
            /* Floored low on purpose. The old floor was 240, which is not a
               minimum so much as an instruction to leave 240px of nothing under
               anything smaller. */
            if (!Number.isFinite(value)) return;
            setReported(Math.max(HEIGHT_MIN, Math.min(HEIGHT_MAX, Math.round(value))));
            setUnreported(false);
        };
        window.addEventListener("message", resize);
        return () => window.removeEventListener("message", resize);
    }, [id]);

    /* Some content will never answer. Waiting on it forever would leave the
       frame hidden and the turn a blank gap, so the wait has an end. */
    useEffect(() => {
        if (!loaded || reported !== null) return;
        const timer = window.setTimeout(() => setUnreported(true), REPORT_GRACE_MS);
        return () => window.clearTimeout(timer);
    }, [loaded, reported]);

    /* And some never load. Nothing is coming after that, so the frame stops
       claiming to be on its way. */
    useEffect(() => {
        if (loaded) return;
        const timer = window.setTimeout(() => setStalled(true), LOAD_DEADLINE_MS);
        return () => window.clearTimeout(timer);
    }, [loaded]);

    /* Ready means: it has loaded, and it has either told us its height or had
       long enough to. Until then the frame holds a reservation and shows
       nothing, so no one watches a widget render at the wrong size and snap.
       A frame that never loaded keeps no reservation at all — leaving it at the
       placeholder would put 180px of nothing above the sentence explaining why
       there is nothing. */
    const { show, height: rendered } = frameState({ loaded, reported, unreported, stalled, expanded, full, ceiling });
    const ready = show === "content";
    const overflows = reported !== null && reported > ceiling;

    return <div
        className={`embed${expanded ? " embed--expanded" : ""}`}
        data-ready={ready ? "" : undefined}
        data-overflows={overflows && !expanded ? "" : undefined}
    >
        <iframe ref={frame} className="embed__frame" title={title} sandbox={sandbox} src={src} srcDoc={document_}
            allow="clipboard-read; clipboard-write; fullscreen"
            referrerPolicy="strict-origin-when-cross-origin"
            onLoad={() => { loadedRef.current = true; setLoaded(true); sendTheme(); registerThisFrame(); }}
            style={{ height: `${rendered}px` }} />
        {show !== "content" && (
            <div className={"embed__waiting" + (show === "stalled" ? " embed__waiting--stalled" : "")} aria-live="polite">
                {show === "stalled" ? title + " could not be shown here." : "Loading " + title + "…"}
            </div>
        )}
        <div className="embed__acts">
            {/* One way out, not two. These are different destinations — a tab
                on the stage, and the browser's own new tab — drawn with the
                same arrow and told apart only by a tooltip nobody hovers for.
                A widget has no stage tab to open and keeps its link; anything
                that does have one takes it, because opening here is the better
                of the two and the stage carries "Open original" anyway. */}
            {onOpen ? (
                <button onClick={onOpen} aria-label={`Open ${title} in a tab`} title="Open in a tab">
                    <ExternalIcon size={15} />
                </button>
            ) : src ? (
                <a href={src} target="_blank" rel="noreferrer" aria-label={`Open ${title} in a new tab`} title="Open in new tab">
                    <ExternalIcon size={15} />
                </a>
            ) : null}
            <button onClick={() => setExpanded(value => !value)} aria-expanded={expanded}
                aria-label={expanded ? `Reduce ${title}` : `Expand ${title}`} title={expanded ? "Smaller" : "Expand"}>
                {expanded ? <CloseIcon size={14} /> : <PlusIcon size={14} />}
            </button>
        </div>
    </div>;
}
