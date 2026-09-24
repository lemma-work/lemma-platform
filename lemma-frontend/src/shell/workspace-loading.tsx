import { ConversationLoading } from "@/thread/conversation-loading";
import { LoadingRows } from "@/ui/loading";

export function WorkspaceLoading({ embedded = false }: { embedded?: boolean }) {
    return <div className={`workspace-loading${embedded ? " workspace-loading--embedded" : ""}`} aria-busy="true">
        {!embedded && <aside className="workspace-loading__rail" aria-hidden="true"><span className="loading-shape workspace-loading__brand" /><LoadingRows rows={4} label="Loading teammates" /></aside>}
        <div className="workspace-loading__main convo-host">
            <div className="workspace-loading__header" aria-hidden="true"><span className="loading-shape" /></div>
            <div className="pane"><div className="pane__inner"><ConversationLoading /></div></div>
            <div className="workspace-loading__composer" aria-hidden="true"><span className="loading-shape" /><span className="loading-shape" /></div>
        </div>
    </div>;
}
