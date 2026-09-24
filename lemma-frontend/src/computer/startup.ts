export interface WorkspaceStatus {
    state: "ready" | "downloading" | "starting" | "asleep" | "unavailable";
    detail?: string | null;
    done_mb?: number | null;
    total_mb?: number | null;
}

export function startupProgress(status: WorkspaceStatus | undefined) {
    if (status?.state !== "downloading" && status?.state !== "starting") return null;
    const done = status.done_mb;
    const total = status.total_mb;
    const measured = status.state === "downloading" && typeof done === "number" && Number.isFinite(done)
        && done >= 0 && typeof total === "number" && Number.isFinite(total) && total > 0;
    return {
        title: status.state === "downloading" ? "Downloading your workspace" : "Starting your workspace",
        detail: status.detail ?? null,
        fraction: measured ? Math.min(1, done / total) : null,
        downloaded: measured ? `${done} of ${total} MB` : null,
    };
}
