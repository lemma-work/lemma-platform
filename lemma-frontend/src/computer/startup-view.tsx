import { startupProgress, type WorkspaceStatus } from "./startup";

export function WorkspaceStartup({ status }: { status: WorkspaceStatus | undefined }) {
    const progress = startupProgress(status);
    if (!progress) return null;
    return (
        <div className="computer-startup" role="status">
            <strong>{progress.title}</strong>
            {progress.detail && <p>{progress.detail}</p>}
            {progress.downloaded && <p>{progress.downloaded}</p>}
            <progress aria-label={progress.title} max={1} value={progress.fraction ?? undefined} />
        </div>
    );
}
