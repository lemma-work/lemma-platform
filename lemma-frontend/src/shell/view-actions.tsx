import { LoadingIndicator } from "@/ui/loading";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type Tab } from "@/data";
import { siteUrl } from "@/session/client";
import { ExternalIcon, RefreshIcon, PlusIcon, HistoryIcon, SettingsIcon, CopyIcon, DownloadIcon, MoreIcon, LinkIcon, ChatIcon, ComputerIcon } from "@/ui/icons";
import { ShareDialog } from "@/thread/share-dialog";
import { useResourceConversation } from "@/thread/use-resource-conversation";
import type { ResourceKind } from "@/thread/resource-conversation";

/** What the tab in front of you is, as a thing that can carry a conversation.
 *
 *  A file is bound by its path rather than its label: two `notes.md` in two
 *  folders are two files, and the label is the same for both.
 */
function boundResource(tab?: Tab): { kind: ResourceKind; name: string } | null {
    if (tab?.kind === "file") return { kind: "file", name: tab.path };
    if (tab?.kind === "table") return { kind: "table", name: tab.name };
    if (tab?.kind === "app") return { kind: "app", name: tab.label };
    return null;
}

export function ViewActions({ tab, podId, teammate, onNew, onHistory, onComputer, onReload, onDiscuss }: {
    tab?: Tab; podId: string; onNew: () => void; onHistory: () => void; onComputer: () => void; onReload: () => void;
    /** Whose conversation it is, so the action can say whose. */
    teammate?: string;
    /** Hand a conversation back to the shell, which owns which one is open. */
    onDiscuss?: (conversationId: string) => void;
}) {
    const cache = useQueryClient();
    const discussion = useResourceConversation(podId);
    const resource = boundResource(tab);
    const sample = source.label === "sample";
    /* One action, on every view that is a thing rather than a list. The
       alternative was a bespoke button per view, which is three places to
       change the wording and three chances for them to disagree. */
    const ask = resource && onDiscuss && !sample ? (
        <button
            disabled={Boolean(discussion.opening)}
            title={"Talk to " + (teammate ?? "the teammate") + " about this"}
            onClick={async () => {
                const id = await discussion.open(resource.kind, resource.name);
                if (id) onDiscuss(id);
            }}
        >
            <ChatIcon size={17} />
            <span>{discussion.opening ? <LoadingIndicator inline label="Loading" /> : "Ask " + (teammate ?? "the teammate")}</span>
        </button>
    ) : null;
    const path = tab?.kind === "file" ? tab.path : "";
    const file = useQuery({ queryKey: ["file", podId, path], queryFn: () => source.readFile(podId, path), enabled: Boolean(path), staleTime: 5 * 60_000 });
    const [feedback, setFeedback] = useState("");
    const [sharing, setSharing] = useState(false);
    const [downloading, setDownloading] = useState(false);
    const download = async () => {
        if (!file.data || downloading) return;
        setDownloading(true); setFeedback("");
        try {
            const data = file.data;
            let blob: Blob;
            if (data.text !== undefined) blob = new Blob([data.text], {type:data.mime});
            else blob = await source.downloadFile(podId, path);
            const url = URL.createObjectURL(blob);
            const link = document.createElement("a"); link.href = url; link.download = data.name;
            document.body.append(link); link.click(); link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 60000);
        } catch { setFeedback("Download failed. Try opening the original."); }
        finally { setDownloading(false); }
    };
    const copy = async () => {
        try { if (!file.data?.appUrl) return; await navigator.clipboard.writeText(file.data.appUrl); setFeedback("Link copied"); }
        catch { setFeedback("Could not copy link"); }
    };
    let primary;
    let secondary;
    if (tab?.kind === "app") {
        primary = <a href={tab.url} target="_blank" rel="noreferrer" title="Open app in new tab"><ExternalIcon size={17}/><span>Open</span></a>;
        secondary = <button onClick={onReload} title="Reload app"><RefreshIcon size={17}/><span>Reload</span></button>;
    } else if (tab?.kind === "file") {
        primary = <button disabled={!file.data || downloading} onClick={() => void download()} title="Download document"><DownloadIcon size={17}/><span>{downloading ? "Downloading…" : "Download"}</span></button>;
        secondary = <><button disabled={!file.data} onClick={() => setSharing(true)} title="Share this document"><LinkIcon size={17}/><span>Share</span></button><button disabled={!file.data?.appUrl} onClick={() => void copy()} title="Copy document link"><CopyIcon size={17}/><span>Copy link</span></button>{file.data?.appUrl && <a href={file.data.appUrl} target="_blank" rel="noreferrer" title="Open original document"><ExternalIcon size={17}/><span>Open original</span></a>}</>;
    } else if (tab?.kind === "library" || tab?.kind === "table") {
        primary = <button onClick={() => void cache.invalidateQueries({ queryKey: tab.kind === "library" ? ["library", podId] : ["table", podId, tab.name] })} title="Refresh resources"><RefreshIcon size={17}/><span>Refresh</span></button>;
    } else if (tab?.kind === "profile") {
        primary = <a href={`${siteUrl()}/pod/${encodeURIComponent(podId)}/settings`} target="_blank" rel="noreferrer" title="Teammate settings"><SettingsIcon size={17}/><span>Settings</span></a>;
        /* Both keys, because they are two caches over one list: the agents on
           this page and whichever one is open. Refreshing one and not the
           other is how a row goes on saying the old thing under an instruction
           that was edited elsewhere. */
        secondary = <button onClick={() => { void cache.invalidateQueries({ queryKey: ["profile", podId] }); void cache.invalidateQueries({ queryKey: ["agents", podId] }); void cache.invalidateQueries({ queryKey: ["agent", podId] }); }} title="Read this page again"><RefreshIcon size={17}/><span>Refresh</span></button>;
    } else if (tab?.kind === "computer") {
        primary = <button onClick={() => void cache.invalidateQueries({ queryKey: ["computer"] })} title="Look again"><RefreshIcon size={17}/><span>Refresh</span></button>;
    } else {
        primary = <button onClick={onNew} title="New conversation"><PlusIcon size={17}/><span>New conversation</span></button>;
        /* Beside History rather than in the tab strip, and secondary to both:
           the machine is worth reaching from the conversation it worked in,
           and is not something anybody opens a teammate to look at. */
        secondary = <>
            <button onClick={onHistory} title="Conversation history"><HistoryIcon size={17}/><span>History</span></button>
            {!sample && <button onClick={onComputer} title="The computer this teammate works on"><ComputerIcon size={17}/><span>Computer</span></button>}
        </>;
    }
    if (ask) secondary = <>{ask}{secondary}</>;

    return <div className="view-actions" aria-label="View actions">
        {primary}<div className="view-actions__secondary">{secondary}</div>
        {secondary && <details className="view-actions__more"><summary aria-label="More view actions" title="More view actions"><MoreIcon size={19}/></summary><div>{secondary}</div></details>}
        {discussion.problem && <button className="view-actions__feedback" role="alert" onClick={discussion.clearProblem}>{discussion.problem}</button>}
        {feedback && <button className="view-actions__feedback" role="status" onClick={() => setFeedback("")}>{feedback}</button>}
        {sharing && file.data && <ShareDialog podId={podId} path={path} name={file.data.name} appUrl={file.data.appUrl} onClose={() => setSharing(false)} />}
    </div>;
}
