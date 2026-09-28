import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type Tab } from "@/data";
import { ExternalIcon, RefreshIcon, PlusIcon, HistoryIcon, CopyIcon, DownloadIcon, MoreIcon, LinkIcon, ComputerIcon } from "@/ui/icons";
import { ShareDialog } from "@/thread/share-dialog";
import { saveFile } from "@/thread/save-file";
import { copyText } from "@/desktop/clipboard";
import { isDesktop } from "@/desktop/bridge";
import { useAppFrame } from "@/desktop/pod-apps";

/** Reload, only where there is a frame to reload. Where the app opens in a
 *  window of its own there is none, and the button did nothing. Its own
 *  component so the frame is read only for app tabs. */
function AppReload({ url, onReload }: { url: string; onReload: () => void }) {
    if (useAppFrame(url).kind !== "frame") return null;
    return <button onClick={onReload} title="Reload app"><RefreshIcon size={17}/><span>Reload</span></button>;
}

export function ViewActions({ tab, podId, onNew, onHistory, onComputer, onReload }: {
    tab?: Tab; podId: string; onNew: () => void; onHistory: () => void; onComputer: () => void; onReload: () => void;
}) {
    const cache = useQueryClient();
    const sample = source.label === "sample";
    const path = tab?.kind === "file" ? tab.path : "";
    const file = useQuery({ queryKey: ["file", podId, path], queryFn: () => source.readFile(podId, path), enabled: Boolean(path), staleTime: 5 * 60_000 });
    const [feedback, setFeedback] = useState("");
    const [sharing, setSharing] = useState(false);
    const [downloading, setDownloading] = useState(false);
    const download = async () => {
        if (!file.data || downloading) return;
        setDownloading(true); setFeedback("");
        try { await saveFile(podId, file.data); }
        catch { setFeedback("Download failed. Please try again."); }
        finally { setDownloading(false); }
    };
    const copy = async () => {
        try { if (!file.data?.appUrl) return; await copyText(file.data.appUrl); setFeedback("Link copied"); }
        catch { setFeedback("Could not copy link"); }
    };
    let primary;
    let secondary;
    if (tab?.kind === "app") {
        /* In the desktop app a new window for a published app is routed to
           an app window of its own, not a browser tab — so it says that. */
        primary = <a href={tab.url} target="_blank" rel="noreferrer" title={isDesktop() ? "Open in its own window" : "Open app in new tab"}><ExternalIcon size={17}/><span>Open</span></a>;
        secondary = <AppReload url={tab.url} onReload={onReload} />;
    } else if (tab?.kind === "file") {
        primary = <button disabled={!file.data || downloading} onClick={() => void download()} title="Download document"><DownloadIcon size={17}/><span>{downloading ? "Downloading…" : "Download"}</span></button>;
        secondary = <><button disabled={!file.data} onClick={() => setSharing(true)} title="Share this document"><LinkIcon size={17}/><span>Share</span></button><button disabled={!file.data?.appUrl} onClick={() => void copy()} title="Copy document link"><CopyIcon size={17}/><span>Copy link</span></button></>;
    } else if (tab?.kind === "library" || tab?.kind === "table") {
        primary = <button onClick={() => void cache.invalidateQueries({ queryKey: tab.kind === "library" ? ["library", podId] : ["table", podId, tab.name] })} title="Refresh resources"><RefreshIcon size={17}/><span>Refresh</span></button>;
    } else if (tab?.kind === "profile") {
        /* Both keys, because they are two caches over one list: the agents on
           this page and whichever one is open. Refreshing one and not the
           other is how a row goes on saying the old thing under an instruction
           that was edited elsewhere. */
        primary = <button onClick={() => { void cache.invalidateQueries({ queryKey: ["profile", podId] }); void cache.invalidateQueries({ queryKey: ["agents", podId] }); void cache.invalidateQueries({ queryKey: ["agent", podId] }); }} title="Read this page again"><RefreshIcon size={17}/><span>Refresh</span></button>;
    } else if (tab?.kind === "computer") {
        primary = <button onClick={() => void cache.invalidateQueries({ queryKey: ["computer"] })} title="Look again"><RefreshIcon size={17}/><span>Refresh</span></button>;
    } else {
        primary = <button onClick={onNew} title="New conversation"><PlusIcon size={17}/><span>New conversation</span></button>;
        /* Beside History rather than in the tab strip, and secondary to both:
           the machine is worth reaching from the conversation it worked in,
           and is not something anybody opens a teammate to look at. */
        secondary = <>
            {/* Not on the History tab itself, where it would open what is open. */}
            {tab?.kind !== "history" && <button className="view-actions__icon" onClick={onHistory} title="History" aria-label="History"><HistoryIcon size={17}/><span>History</span></button>}
            {!sample && <button className="view-actions__icon" onClick={onComputer} title="The computer it works on" aria-label="Computer"><ComputerIcon size={17}/><span>Computer</span></button>}
        </>;
    }

    return <div className="view-actions" aria-label="View actions">
        {primary}<div className="view-actions__secondary">{secondary}</div>
        {secondary && <details className="view-actions__more"><summary aria-label="More view actions" title="More view actions"><MoreIcon size={19}/></summary><div>{secondary}</div></details>}
        {feedback && <button className="view-actions__feedback" role="status" onClick={() => setFeedback("")}>{feedback}</button>}
        {sharing && file.data && <ShareDialog podId={podId} path={path} name={file.data.name} appUrl={file.data.appUrl} onClose={() => setSharing(false)} />}
    </div>;
}
